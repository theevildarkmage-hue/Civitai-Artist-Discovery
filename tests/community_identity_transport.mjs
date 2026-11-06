// Shared synthetic provider adapter for older functional fixtures. Security-negative
// scope cases use their own transport and never this default read-only fixture.
export function identityTransport(transport) {
  return async(input,options)=>{
    if(String(input)!=='https://civitai.com/api/v1/me')return transport(input,options);
    const response=await transport('https://auth.civitai.com/api/auth/oauth/userinfo',options);
    if(!response.ok)return response;
    const user=await response.json();
    return Response.json({...user,id:user.id??Number(user.sub),tokenScope:33,subject:{type:'oauth',id:'synthetic-client'}});
  };
}
