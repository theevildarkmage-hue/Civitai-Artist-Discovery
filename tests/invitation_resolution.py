import test_safety,sys,unittest
sys.path.insert(0,str(test_safety.ROOT))
from discovery.invitations import resolve_account

class ResolutionTests(unittest.TestCase):
    def test_fixed_host_roundtrip(self):
        calls=[]
        def fetch(url):calls.append(url);return {'result':{'data':{'json':{'id':7,'username':'SyntheticMember'}}}}
        self.assertEqual(resolve_account('https://civitai.red/user/SyntheticMember',fetch)['id'],7)
        self.assertEqual(len(calls),2);self.assertTrue(all(x.startswith('https://civitai.red/api/trpc/user.') for x in calls))
    def test_invalid_or_changed_profiles(self):
        for value in ['https://evil.invalid/user/Test','https://civitai.red@evil.invalid/user/Test','https://civitai.red/user/Test?x=1','Test/../Other']:
            with self.assertRaises(ValueError):resolve_account(value,lambda url:self.fail('Unexpected fetch'))
        with self.assertRaises(ValueError):resolve_account('SyntheticMember',lambda url:{'result':{'data':{'json':{'id':7,'username':'WrongMember'}}}})

if __name__=='__main__':unittest.main()
