import { describe, expect, it } from "vitest";
import type { ReactionsService } from "../../reactions/index.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type {
  FeedPlaceRow,
  FeedRecommendationClient,
  FeedRecommendationItem,
  FeedRecommendationRequest,
  FeedStoreContract
} from "../common/feed.types.js";
import {
  createFeedPlacesService,
  FeedRecommendationCache
} from "../services/feed.service.js";

function feedRow(overrides: Partial<FeedPlaceRow> = {}): FeedPlaceRow {
  return {
    id: 1,
    source: "google",
    source_id: "place_1",
    name: "Origo Coffee",
    country: "RO",
    city: "Bucharest",
    category: "cafe",
    primary_type: "coffee_shop",
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    price_level: 2,
    reviews_count: 120,
    map_visibility_score: 91,
    ai_card_summary: "Calm specialty coffee.",
    ai_place_type_summary: "Coffee shop",
    ai_vibe: "quiet",
    ai_the_move: null,
    ai_tags: ["quiet", "coffee"],
    formatted_address: "Bucharest",
    distance_m: null,
    primary_photo_path: null,
    primary_photo_url: null,
    primary_photo_width: null,
    primary_photo_height: null,
    primary_photo_source: null,
    ...overrides
  };
}

function createStore(
  overrides: Partial<FeedStoreContract> = {}
): FeedStoreContract {
  return {
    async getUserSignals() {
      return {
        favouritesPlaceIds: ["place_1"],
        wantToGoPlaceIds: ["place_2"],
        dislikePlaceIds: ["place_4"],
        hidePlaceIds: ["place_5"]
      };
    },
    async feedPlacesBySourceIds(sourceIds) {
      return sourceIds.map((sourceId, index) =>
        feedRow({
          id: index + 1,
          source_id: sourceId,
          name: `Place ${sourceId}`
        })
      );
    },
    async fallbackFeedPlaces() {
      return [feedRow({ id: 9, source_id: "fallback_1", name: "Fallback" })];
    },
    ...overrides
  };
}

function createRecommendations(count: number): FeedRecommendationItem[] {
  return Array.from({ length: count }, (_, index) => ({
    rank: index + 1,
    place_id: `place_${index + 1}`,
    score: Math.max(0.01, 1 - index * 0.01)
  }));
}

function createClient(): {
  client: FeedRecommendationClient;
  calls: () => number;
  requests: () => FeedRecommendationRequest[];
} {
  let callCount = 0;
  const requests: FeedRecommendationRequest[] = [];
  const recommendations = [
    {
      rank: 1,
      place_id: "place_2",
      score: 0.98
    },
    {
      rank: 2,
      place_id: "place_3",
      score: 0.75
    }
  ];

  return {
    calls: () => callCount,
    requests: () => requests,
    client: {
      async personalizedPlaces(request) {
        callCount += 1;
        requests.push(request);
        expect(request.favourites_place_ids).toEqual(["place_1"]);
        expect(request.want_to_go_place_ids).toEqual(["place_2"]);
        expect(request.dislike_place_ids).toEqual(["place_4"]);
        expect(request.hide_place_ids).toEqual(["place_5"]);

        return {
          user_id: request.user_id,
          algorithm_version: "embedding_recommender_v1",
          embedding_run_id: "test-run",
          input_summary: {
            favourites_count: 1,
            want_to_go_count: 1,
            valid_input_count: 2,
            invalid_place_ids: []
          },
          recommendations
        };
      }
    }
  };
}

function createSavedService(
  overrides: Partial<SavedPlacesService> = {}
): SavedPlacesService {
  const unused = async () => {
    throw new Error("not used");
  };

  return {
    getSavedDashboard: unused,
    getCollectionDetail: unused,
    savePlace: unused,
    unsavePlace: unused,
    createCollection: unused,
    updateCollection: unused,
    deleteCollection: unused,
    addPlaceToCollection: unused,
    removePlaceFromCollection: unused,
    reorderCollectionPlaces: unused,
    getSavedPlaceIds: async () => new Set(),
    getSavedPlaceStates: unused,
    ...overrides
  } as SavedPlacesService;
}

function createReactionsService(
  overrides: Partial<ReactionsService> = {}
): ReactionsService {
  const unused = async () => {
    throw new Error("not used");
  };

  return {
    setReaction: unused,
    deleteReaction: unused,
    getReactions: unused,
    getReactionMap: async () => new Map(),
    ...overrides
  } as ReactionsService;
}

describe("feed places service", () => {
  it("calls recommendation service and preserves hydrated order", async () => {
    const { client, requests } = createClient();
    const service = createFeedPlacesService(
      createStore(),
      client,
      createSavedService({
        async getSavedPlaceIds(userId, placeIds) {
          expect(userId).toBe("user-1");
          expect(placeIds).toEqual([1, 2]);
          return new Set([2]);
        }
      }),
      new FeedRecommendationCache(),
      createReactionsService({
        async getReactionMap(userId, placeIds) {
          expect(userId).toBe("user-1");
          expect(placeIds).toEqual([1, 2]);
          return new Map([[1, "hide"]]);
        }
      })
    );

    await expect(
      service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          debug: false
        },
        user: {
          id: "user-1",
          email: "user@example.com"
        }
      })
    ).resolves.toMatchObject({
      feed: {
        personalizationStatus: "personalized",
        cacheStatus: "miss",
        algorithmVersion: "embedding_recommender_v1"
      },
      inputSummary: {
        favouritesCount: 1,
        wantToGoCount: 1,
        validInputCount: 2
      },
      places: [
        {
          sourceId: "place_2",
          rank: 1,
          matchScore: 98,
          isSaved: false,
          reaction: "hide"
        },
        {
          sourceId: "place_3",
          rank: 2,
          matchScore: 75,
          isSaved: true,
          reaction: null
        }
      ]
    });

    expect(requests()[0]?.limit).toBe(200);
  });

  it("uses cached recommendations for repeated requests with same signals", async () => {
    const { client, calls } = createClient();
    const service = createFeedPlacesService(
      createStore(),
      client,
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );
    const input = {
      query: {
        limit: 20,
        offset: 0,
        sort: "relevance" as const,
        debug: false
      },
      user: {
        id: "user-1",
        email: "user@example.com"
      }
    };

    const first = await service(input);
    const second = await service(input);

    expect(calls()).toBe(1);
    expect(first.feed.cacheStatus).toBe("miss");
    expect(second.feed.cacheStatus).toBe("hit");
  });

  it("falls back for anonymous users", async () => {
    const service = createFeedPlacesService(
      createStore(),
      createClient().client,
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    await expect(
      service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          debug: false
        },
        user: null
      })
    ).resolves.toMatchObject({
      feed: {
        personalizationStatus: "anonymous_fallback",
        cacheStatus: "not_applicable"
      },
      places: [
        {
          sourceId: "fallback_1",
          matchScore: 91,
          reaction: null
        }
      ]
    });
  });

  it("falls back when the recommendation service fails", async () => {
    const service = createFeedPlacesService(
      createStore(),
      {
        async personalizedPlaces() {
          throw new Error("boom");
        }
      },
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    await expect(
      service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          debug: false
        },
        user: {
          id: "user-1",
          email: "user@example.com"
        }
      })
    ).resolves.toMatchObject({
      feed: {
        personalizationStatus: "recommendation_service_fallback",
        cacheStatus: "miss"
      },
      places: [
        {
          sourceId: "fallback_1"
        }
      ]
    });
  });

  it("filters disliked and hidden places from fallback responses", async () => {
    const service = createFeedPlacesService(
      createStore({
        async getUserSignals() {
          return {
            favouritesPlaceIds: ["place_1"],
            wantToGoPlaceIds: [],
            dislikePlaceIds: ["fallback_1"],
            hidePlaceIds: ["fallback_2"]
          };
        },
        async fallbackFeedPlaces() {
          return [
            feedRow({ id: 9, source_id: "fallback_1", name: "Disliked" }),
            feedRow({ id: 10, source_id: "fallback_2", name: "Hidden" }),
            feedRow({ id: 11, source_id: "fallback_3", name: "Visible" })
          ];
        }
      }),
      {
        async personalizedPlaces() {
          throw new Error("boom");
        }
      },
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    await expect(
      service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          debug: false
        },
        user: {
          id: "user-1",
          email: "user@example.com"
        }
      })
    ).resolves.toMatchObject({
      feed: {
        personalizationStatus: "recommendation_service_fallback"
      },
      places: [
        {
          sourceId: "fallback_3"
        }
      ]
    });
  });

  it("returns a filtered no-signals fallback for users with only dislikes and hides", async () => {
    const service = createFeedPlacesService(
      createStore({
        async getUserSignals() {
          return {
            favouritesPlaceIds: [],
            wantToGoPlaceIds: [],
            dislikePlaceIds: ["fallback_1"],
            hidePlaceIds: []
          };
        },
        async fallbackFeedPlaces() {
          return [
            feedRow({ id: 9, source_id: "fallback_1", name: "Blocked" }),
            feedRow({ id: 10, source_id: "fallback_2", name: "Visible" })
          ];
        }
      }),
      createClient().client,
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    await expect(
      service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          debug: false
        },
        user: {
          id: "user-1",
          email: "user@example.com"
        }
      })
    ).resolves.toMatchObject({
      feed: {
        personalizationStatus: "no_signals_fallback"
      },
      places: [
        {
          sourceId: "fallback_2"
        }
      ]
    });
  });

  it("invalidates the recommendation cache when reaction lists change", async () => {
    let signals = {
      favouritesPlaceIds: ["place_1"],
      wantToGoPlaceIds: ["place_2"],
      dislikePlaceIds: [] as string[],
      hidePlaceIds: [] as string[]
    };
    const { client, calls } = createClient();
    const service = createFeedPlacesService(
      createStore({
        async getUserSignals() {
          return signals;
        }
      }),
      client,
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );
    const input = {
      query: {
        limit: 20,
        offset: 0,
        sort: "relevance" as const,
        debug: false
      },
      user: {
        id: "user-1",
        email: "user@example.com"
      }
    };

    await service(input);
    signals = {
      ...signals,
      dislikePlaceIds: ["place_4"]
    };
    const second = await service(input);

    expect(calls()).toBe(2);
    expect(second.feed.cacheStatus).toBe("miss");
  });

  it("paginates personalized snapshots past rank 50", async () => {
    const recommendations = createRecommendations(100);
    const requests: FeedRecommendationRequest[] = [];
    const service = createFeedPlacesService(
      createStore({
        async feedPlacesBySourceIds(sourceIds, _query, limit) {
          expect(limit).toBe(200);
          return sourceIds.map((sourceId, index) =>
            feedRow({
              id: index + 1,
              source_id: sourceId,
              name: `Place ${sourceId}`
            })
          );
        }
      }),
      {
        async personalizedPlaces(request) {
          requests.push(request);
          return {
            user_id: request.user_id,
            algorithm_version: "embedding_recommender_v1",
            embedding_run_id: "test-run",
            input_summary: {
              favourites_count: 1,
              want_to_go_count: 1,
              valid_input_count: 2,
              invalid_place_ids: []
            },
            recommendations
          };
        }
      },
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    const result = await service({
      query: {
        limit: 30,
        offset: 60,
        sort: "relevance" as const,
        debug: false
      },
      user: {
        id: "user-1",
        email: "user@example.com"
      }
    });

    expect(requests[0]?.limit).toBe(200);
    expect(result.places).toHaveLength(30);
    expect(result.places[0]).toMatchObject({
      sourceId: "place_61",
      rank: 61
    });
    expect(result.places.at(-1)).toMatchObject({
      sourceId: "place_90",
      rank: 90
    });
  });

  it("reuses one cached snapshot across page 1 and page 2", async () => {
    const recommendations = createRecommendations(100);
    let callCount = 0;
    const service = createFeedPlacesService(
      createStore(),
      {
        async personalizedPlaces(request) {
          callCount += 1;
          return {
            user_id: request.user_id,
            algorithm_version: "embedding_recommender_v1",
            embedding_run_id: "test-run",
            input_summary: {
              favourites_count: 1,
              want_to_go_count: 1,
              valid_input_count: 2,
              invalid_place_ids: []
            },
            recommendations
          };
        }
      },
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    const pageOne = await service({
      query: {
        limit: 20,
        offset: 0,
        sort: "relevance" as const,
        debug: false
      },
      user: {
        id: "user-1",
        email: "user@example.com"
      }
    });
    const pageTwo = await service({
      query: {
        limit: 30,
        offset: 20,
        sort: "relevance" as const,
        debug: false
      },
      user: {
        id: "user-1",
        email: "user@example.com"
      }
    });

    expect(callCount).toBe(1);
    expect(pageOne.feed.cacheStatus).toBe("miss");
    expect(pageTwo.feed.cacheStatus).toBe("hit");
    expect(pageOne.places.map((place) => place.rank)).toEqual(
      Array.from({ length: 20 }, (_, index) => index + 1)
    );
    expect(pageTwo.places.map((place) => place.rank)).toEqual(
      Array.from({ length: 30 }, (_, index) => index + 21)
    );
    expect(
      new Set([
        ...pageOne.places.map((place) => place.sourceId),
        ...pageTwo.places.map((place) => place.sourceId)
      ]).size
    ).toBe(50);
  });

  it("returns an empty personalized page when offset is beyond the snapshot", async () => {
    const service = createFeedPlacesService(
      createStore(),
      {
        async personalizedPlaces(request) {
          return {
            user_id: request.user_id,
            algorithm_version: "embedding_recommender_v1",
            embedding_run_id: "test-run",
            input_summary: {
              favourites_count: 1,
              want_to_go_count: 1,
              valid_input_count: 2,
              invalid_place_ids: []
            },
            recommendations: createRecommendations(70)
          };
        }
      },
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    const result = await service({
      query: {
        limit: 30,
        offset: 70,
        sort: "relevance" as const,
        debug: false
      },
      user: {
        id: "user-1",
        email: "user@example.com"
      }
    });

    expect(result.feed.personalizationStatus).toBe("personalized");
    expect(result.places).toEqual([]);
  });

  it("paginates fallback snapshots past rank 50", async () => {
    const service = createFeedPlacesService(
      createStore({
        async fallbackFeedPlaces(_query, limit) {
          expect(limit).toBe(200);
          return Array.from({ length: 100 }, (_, index) =>
            feedRow({
              id: index + 1,
              source_id: `fallback_${index + 1}`,
              name: `Fallback ${index + 1}`
            })
          );
        }
      }),
      createClient().client,
      createSavedService(),
      new FeedRecommendationCache(),
      createReactionsService()
    );

    const result = await service({
      query: {
        limit: 30,
        offset: 60,
        sort: "relevance" as const,
        debug: false
      },
      user: null
    });

    expect(result.feed.personalizationStatus).toBe("anonymous_fallback");
    expect(result.places).toHaveLength(30);
    expect(result.places[0]).toMatchObject({
      sourceId: "fallback_61",
      rank: 61
    });
    expect(result.places.at(-1)).toMatchObject({
      sourceId: "fallback_90",
      rank: 90
    });
  });

  describe("category filter", () => {
    it("passes flattened bucket keywords to the fallback store", async () => {
      let receivedKeywords: string[] | null = null;
      const service = createFeedPlacesService(
        createStore({
          async fallbackFeedPlaces(_query, _limit, categoryKeywords) {
            receivedKeywords = categoryKeywords;
            return [
              feedRow({ id: 9, source_id: "fallback_1", primary_type: "wine bar" })
            ];
          }
        }),
        createClient().client,
        createSavedService(),
        new FeedRecommendationCache(),
        createReactionsService()
      );

      const result = await service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          category: ["bar"],
          debug: false
        },
        user: null
      });

      expect(receivedKeywords).toEqual(expect.arrayContaining(["bar", "pub"]));
      expect(receivedKeywords).not.toEqual(expect.arrayContaining(["museum"]));
      expect(result.places).toHaveLength(1);
    });

    it("filters the personalized snapshot and re-ranks positionally", async () => {
      const { client } = createClient();
      const service = createFeedPlacesService(
        createStore({
          async feedPlacesBySourceIds(sourceIds) {
            const types: Record<string, string> = {
              place_2: "cafe",
              place_3: "wine bar"
            };
            return sourceIds.map((sourceId, index) =>
              feedRow({
                id: index + 1,
                source_id: sourceId,
                primary_type: types[sourceId] ?? null
              })
            );
          }
        }),
        client,
        createSavedService(),
        new FeedRecommendationCache(),
        createReactionsService()
      );

      const result = await service({
        query: {
          limit: 20,
          offset: 0,
          sort: "relevance" as const,
          category: ["bar"],
          debug: false
        },
        user: { id: "user-1", email: "user@example.com" }
      });

      expect(result.feed.personalizationStatus).toBe("personalized");
      expect(result.places.map((place) => place.sourceId)).toEqual(["place_3"]);
      // gapless positional rank after filtering
      expect(result.places.map((place) => place.rank)).toEqual([1]);
    });
  });

  describe("sort=distance", () => {
    function distanceQuery(overrides: Partial<{ limit: number; offset: number }> = {}) {
      return {
        limit: 20,
        offset: 0,
        lat: 44.43,
        lng: 26.1,
        sort: "distance" as const,
        debug: false,
        ...overrides
      };
    }

    it("re-orders the personalized snapshot by distance with positional rank", async () => {
      const { client } = createClient();
      const service = createFeedPlacesService(
        createStore({
          async feedPlacesBySourceIds(sourceIds) {
            const distances: Record<string, number> = {
              place_2: 500,
              place_3: 100
            };
            return sourceIds.map((sourceId, index) =>
              feedRow({
                id: index + 1,
                source_id: sourceId,
                name: `Place ${sourceId}`,
                distance_m: distances[sourceId] ?? null
              })
            );
          }
        }),
        client,
        createSavedService(),
        new FeedRecommendationCache(),
        createReactionsService()
      );

      const result = await service({
        query: distanceQuery(),
        user: { id: "user-1", email: "user@example.com" }
      });

      expect(result.feed.sort).toBe("distance");
      expect(result.places.map((place) => place.sourceId)).toEqual([
        "place_3",
        "place_2"
      ]);
      expect(result.places.map((place) => place.rank)).toEqual([1, 2]);
      expect(
        result.places.map((place) => place.distanceMeters)
      ).toEqual([100, 500]);
    });

    it("breaks distance ties by relevance order", async () => {
      const { client } = createClient();
      const service = createFeedPlacesService(
        createStore({
          async feedPlacesBySourceIds(sourceIds) {
            return sourceIds.map((sourceId, index) =>
              feedRow({
                id: index + 1,
                source_id: sourceId,
                distance_m: 250
              })
            );
          }
        }),
        client,
        createSavedService(),
        new FeedRecommendationCache(),
        createReactionsService()
      );

      const result = await service({
        query: distanceQuery(),
        user: { id: "user-1", email: "user@example.com" }
      });

      expect(result.places.map((place) => place.sourceId)).toEqual([
        "place_2",
        "place_3"
      ]);
    });

    it("continues the distance ordering across offset windows", async () => {
      const recommendations = createRecommendations(100);
      const service = createFeedPlacesService(
        createStore({
          async feedPlacesBySourceIds(sourceIds) {
            // Distance order is the exact reverse of relevance order.
            return sourceIds.map((sourceId, index) =>
              feedRow({
                id: index + 1,
                source_id: sourceId,
                distance_m: (sourceIds.length - index) * 10
              })
            );
          }
        }),
        {
          async personalizedPlaces(request) {
            return {
              user_id: request.user_id,
              algorithm_version: "embedding_recommender_v1",
              embedding_run_id: "test-run",
              input_summary: {
                favourites_count: 1,
                want_to_go_count: 1,
                valid_input_count: 2,
                invalid_place_ids: []
              },
              recommendations
            };
          }
        },
        createSavedService(),
        new FeedRecommendationCache(),
        createReactionsService()
      );

      const pageOne = await service({
        query: distanceQuery({ limit: 20, offset: 0 }),
        user: { id: "user-1", email: "user@example.com" }
      });
      const pageTwo = await service({
        query: distanceQuery({ limit: 20, offset: 20 }),
        user: { id: "user-1", email: "user@example.com" }
      });

      const pageOneDistances = pageOne.places.map(
        (place) => place.distanceMeters ?? 0
      );
      const pageTwoDistances = pageTwo.places.map(
        (place) => place.distanceMeters ?? 0
      );

      expect(pageOneDistances).toEqual([...pageOneDistances].sort((a, b) => a - b));
      expect(pageTwoDistances[0]).toBeGreaterThanOrEqual(
        pageOneDistances.at(-1) ?? 0
      );
      expect(pageOne.places.map((place) => place.rank)).toEqual(
        Array.from({ length: 20 }, (_, index) => index + 1)
      );
      expect(pageTwo.places.map((place) => place.rank)).toEqual(
        Array.from({ length: 20 }, (_, index) => index + 21)
      );
    });

    it("sorts the fallback snapshot by distance too", async () => {
      const service = createFeedPlacesService(
        createStore({
          async fallbackFeedPlaces() {
            return [
              feedRow({ id: 9, source_id: "fallback_1", distance_m: 300 }),
              feedRow({ id: 10, source_id: "fallback_2", distance_m: 100 }),
              feedRow({ id: 11, source_id: "fallback_3", distance_m: 200 })
            ];
          }
        }),
        createClient().client,
        createSavedService(),
        new FeedRecommendationCache(),
        createReactionsService()
      );

      const result = await service({
        query: distanceQuery(),
        user: null
      });

      expect(result.feed.personalizationStatus).toBe("anonymous_fallback");
      expect(result.feed.sort).toBe("distance");
      expect(result.places.map((place) => place.sourceId)).toEqual([
        "fallback_2",
        "fallback_3",
        "fallback_1"
      ]);
      expect(result.places.map((place) => place.rank)).toEqual([1, 2, 3]);
    });
  });
});
