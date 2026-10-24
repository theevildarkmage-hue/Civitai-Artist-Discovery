// Identity-only candidate. Requires the reviewed identity-session migration and
// server-only challenge/metadata configuration before a hosted rollout.
import {createIdentityCommunityHandler} from './identity-handler.ts';
Deno.serve(createIdentityCommunityHandler());
