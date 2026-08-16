export {
  registerEventsModule,
  type EventsModuleOptions
} from "./events.module.js";
export {
  createEventsService,
  eventsService
} from "./services/events.service.js";
export { EventsStore } from "./stores/events.store.js";
export {
  KNOWN_EVENT_TYPES,
  MAX_BATCH_BODY_BYTES,
  MAX_EVENTS_PER_BATCH
} from "./common/events.schemas.js";
export type {
  EventRow,
  EventsAcceptedResponse,
  EventsBatchBody,
  EventsServiceContract,
  EventsStoreContract
} from "./common/events.types.js";
export * from "./common/events.openapi.js";
