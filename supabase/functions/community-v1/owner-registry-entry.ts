// Acceptance artifact only. The normal index.ts never selects owner-only mode.
// Configuration is provisioned through Supabase project secrets and read by
// the handler. No credentials, owner ID, or deadline are embedded here.
import { createCommunityHandler } from './handler.ts';
Deno.serve(createCommunityHandler({ownerOnly:true,invitationOnly:true,distributedLimit:true}));
