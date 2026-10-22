// Production entry point: opaque Civitai OAuth is verified inside the handler.
import { createCommunityHandler } from './handler.ts';
// Temporary staging boundary: environment/body/header flags cannot turn this off.
Deno.serve(createCommunityHandler({ownerOnly:true}));
