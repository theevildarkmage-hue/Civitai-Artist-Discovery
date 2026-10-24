"""Real Python ES256 signatures interoperate with WebCrypto; transport is simulated."""
import test_safety
import base64,json,secrets,subprocess,unittest
from discovery import cloud_identity,device_identity

class DeviceProofTests(unittest.TestCase):
 def test_python_signature_verified_by_actual_node_webcrypto(self):
  device=device_identity.generate();message='discovery-renew-v1\nsynthetic-hash\nrequest\nattempt\n1000'
  signature=device_identity.sign(device,message)
  code="""let input='';for await(const chunk of process.stdin)input+=chunk;
const v=JSON.parse(input);const key=await crypto.subtle.importKey('jwk',v.key,{name:'ECDSA',namedCurve:'P-256'},false,['verify']);
if(!await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},key,Buffer.from(v.signature,'base64url'),new TextEncoder().encode(v.message)))process.exit(1);
console.log('PASS: Python device signature verified by WebCrypto');"""
  result=subprocess.run(['node','--input-type=module','-e',code],input=json.dumps({'key':device['public'],'signature':signature,'message':message}),capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0);self.assertIn('PASS:',result.stdout)
 def test_device_exchange_allowlist_and_secure_local_retention(self):
  calls=[]
  def send(url,token,body):
   self.assertEqual(url,cloud_identity.ENDPOINT);self.assertIsNone(token);calls.append(body)
   if body['operation']=='identityChallenge':return {'apiVersion':2,'nonce':'n'*43,'audience':'client','expiresAt':1300,'challenge':'synthetic-envelope','devicePublicKey':body['devicePublicKey']}
   self.assertEqual(set(body),{'apiVersion','operation','challenge','identityAssertion','deviceSignature'})
   return {'apiVersion':2,'session':'sdc_'+'s'*43,'expiresAt':44000,'member':{'id':7},
    'renewal':{'credential':'sdr_'+'r'*43,'expiresAt':1000+30*86400,'absoluteExpiresAt':1000+90*86400}}
  pending=cloud_identity.challenge('client',send=send,clock=lambda:1000)
  value=cloud_identity.exchange('header.payload.signature',pending,7,send=send,clock=lambda:1000)
  self.assertIn('device',value['renewal']);self.assertNotIn(pending['_device']['private'],json.dumps(calls))
 def test_renewal_is_signed_no_primary_credentials_or_claimed_member(self):
  device=device_identity.generate();calls=[]
  session={'member_id':7,'renewal':{'credential':'sdr_'+'r'*43,'device':device,'pending_request':'p'*43,'expires_at':2000,'absolute_expires_at':3000}}
  def send(url,token,body):
   self.assertIsNone(token);calls.append(body)
   self.assertEqual(set(body),{'apiVersion','operation','credential','requestId','attemptNonce','issuedAt','signature'})
   self.assertEqual(body['requestId'],'p'*43);self.assertEqual(body['operation'],'identityRenew')
   return {'apiVersion':2,'session':'sdc_'+'s'*43,'expiresAt':2000,'member':{'id':7},
    'renewal':{'credential':'sdr_'+'n'*43,'expiresAt':2000,'absoluteExpiresAt':3000}}
  value=cloud_identity.renew(session,send=send,clock=lambda:1000)
  self.assertNotIn('pending_request',value['renewal']);self.assertNotIn(device['private'],json.dumps(calls))
  self.assertEqual(value['member_id'],7)
 def test_renewal_rejects_account_substitution_and_bad_expiry(self):
  device=device_identity.generate();session={'member_id':7,'renewal':{'credential':'sdr_'+'r'*43,'device':device,'pending_request':'p'*43,'expires_at':2000,'absolute_expires_at':3000}}
  for member in ({'id':8},{'id':True},{}):
   with self.assertRaises(RuntimeError):cloud_identity.renew(session,send=lambda *a:{'apiVersion':2,'session':'sdc_'+'s'*43,'expiresAt':2000,'member':member},clock=lambda:1000)
 def test_persisted_civitai_credentials_never_enter_renewal_transport(self):
  device=device_identity.generate()
  for change in ({'credential':'civitai_access_canary'},{'pending_request':'civitai_refresh_canary'}):
   session={'member_id':7,'renewal':{'credential':'sdr_'+'r'*43,'device':device,'pending_request':'p'*43,'expires_at':2000,'absolute_expires_at':3000,**change}}
   with self.assertRaises(RuntimeError):cloud_identity.renew(session,send=lambda *a:self.fail('Credential entered transport'),clock=lambda:1000)

if __name__=='__main__':unittest.main()
