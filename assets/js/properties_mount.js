/** Mount properties accessibility for a static surface. */
import { enhanceProperties } from "./properties.js";
export function mount(root) { return { dispose: enhanceProperties(root) }; }
