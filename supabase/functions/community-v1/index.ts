// Production entry point: opaque Civitai OAuth is verified inside the handler.
import { createCommunityHandler } from './handler.ts';
// Candidate private-beta boundary; requires the separate reviewed beta migration.
// Missing database configuration/invitations fail closed, never fall back to owner mode.
Deno.serve(createCommunityHandler({invitationOnly:true,galleryWindow:true}));
