import type {
  PlaceReaction,
  ReactionsStoreContract,
  ReactionsServiceContract
} from "../common/reactions.types.js";
import { ReactionsStore } from "../stores/reactions.store.js";

export class ReactionsServiceImpl implements ReactionsServiceContract {
  constructor(private readonly store: ReactionsStoreContract) {}

  async setReaction(userId: string, placeId: number, reaction: PlaceReaction) {
    await this.store.setReaction(userId, placeId, reaction);

    return {
      placeId,
      reaction
    };
  }

  async deleteReaction(userId: string, placeId: number) {
    await this.store.deleteReaction(userId, placeId);
  }

  async getReactions(userId: string) {
    return this.store.listReactions(userId);
  }

  async getReactionMap(userId: string, placeIds: number[]) {
    return this.store.getReactions(userId, placeIds);
  }
}

export function createReactionsService(
  store: ReactionsStoreContract = new ReactionsStore()
) {
  return new ReactionsServiceImpl(store);
}

export const reactionsService = createReactionsService();
