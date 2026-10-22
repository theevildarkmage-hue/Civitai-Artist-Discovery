// Production entry point: opaque Civitai OAuth is verified inside the handler.
import { createCommunityHandler } from './handler.ts';
Deno.serve(createCommunityHandler());
