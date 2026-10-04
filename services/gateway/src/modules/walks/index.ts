export { registerWalksModule, type WalksModuleOptions } from "./walks.module.js";
export {
  createWalksService,
  WalksServiceImpl
} from "./services/walks.service.js";
export { WalksStore } from "./stores/walks.store.js";
export { toCamel, toSnake } from "./common/walks.keys.js";
export type {
  WalksReply,
  WalksServiceContract,
  WalksServiceContract as WalksService,
  WalksSignalsSource,
  WalksStoreContract
} from "./common/walks.types.js";
export * from "./common/walks.openapi.js";
