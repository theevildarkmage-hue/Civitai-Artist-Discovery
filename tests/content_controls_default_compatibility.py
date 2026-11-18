"""Compare the default-on production evaluator with the pre-toggle exclusion contract."""
import test_safety
from dataclasses import replace
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery.community import Artwork,content_profile
from discovery.taste import TasteStore

RULES={'hiddenUsers':[{'id':19,'username':'Hidden'}], 'blockedUsers':[{'id':18,'username':'Blocked'}], 'blockedByUsers':[],
       'hiddenTags':[{'id':11,'name':'topic'},{'id':12,'name':'inactive','hidden':False},
                     {'id':99,'name':'rating-topic','nsfwLevel':4}],
       'hiddenImages':[1000,{'id':1006,'hidden':False}],
       'hiddenImagesImplicit':[{'id':1001,'tagId':11},{'id':1002,'tagId':12},
                               {'id':1003,'tagId':99},{'id':1004,'tagId':999},1005]}

def legacy_allows(art,levels):
    # The original evaluator's imported-control rules, independent of new switches.
    users=RULES['hiddenUsers']+RULES['blockedUsers']+RULES['blockedByUsers']
    creators={u['id'] for u in users if u.get('hidden') is not False}
    names={u['username'].casefold() for u in users if u.get('hidden') is not False}
    tags={t['name'].casefold() for t in RULES['hiddenTags'] if t.get('hidden') is not False}
    tag_ids={t['id'] for t in RULES['hiddenTags'] if t.get('hidden') is not False}
    images={r['id'] if isinstance(r,dict) else r for r in RULES['hiddenImages']+RULES['hiddenImagesImplicit']
            if not isinstance(r,dict) or r.get('hidden') is not False}
    return (art.rating in levels and art.creator_id not in creators and art.username.casefold() not in names
            and art.id not in images and not tags.intersection(art.tags) and not tag_ids.intersection(art.tag_ids)
            and (art.tags_known or not (tags or tag_ids)))

class Compatibility(unittest.TestCase):
    def test_default_on_matches_prior_decisions_for_all_imported_sources(self):
        artworks=[Artwork(i,17,'Visible',1,'local') for i in range(1000,1009)]
        artworks[7]=replace(artworks[7],tags=('rating-topic',),tag_ids=(99,))
        artworks.extend([Artwork(1010,18,'Blocked',1,'local'),Artwork(1011,19,'Hidden',1,'local'),
                         Artwork(1012,17,'Visible',8,'local'),Artwork(1013,17,'Visible',1,'local',tags_known=False)])
        for levels in ((1,2),(1,2,4),(1,2,4,8,16)):
            profile=content_profile(RULES,levels)
            for art in artworks:
                with self.subTest(levels=levels,image=art.id):
                    self.assertEqual(legacy_allows(art,levels),profile.allows(art))
    def test_refreshed_and_reopened_daily_mirrors_preserve_default_on(self):
        with tempfile.TemporaryDirectory(prefix='civitai-default-controls-') as folder:
            store=TasteStore(Path(folder))
            with patch('discovery.taste.auth_status',return_value={'id':7}),patch('discovery.taste.SocialClient.query',return_value=RULES):
                store.import_hidden_preferences()
            with store.connect() as db:db.execute("INSERT INTO archive_image_tags VALUES(1007,'rating-topic')")
            for source in (store,TasteStore(Path(folder))):
                self.assertEqual(source.hidden_image_ids(levels=(1,2,4,8,16)),{1000,1001,1002,1003,1004,1005,1007})
    def test_explicit_pauses_keep_ratings_and_unknown_provenance_enforced(self):
        profile=replace(content_profile(RULES,(1,2)),apply_tag_filters=False,apply_image_filters=False,apply_account_filters=False)
        self.assertTrue(profile.allows(Artwork(1001,18,'Blocked',1,'local')))
        self.assertFalse(profile.allows(Artwork(1004,17,'Visible',1,'local')))
        self.assertFalse(profile.allows(Artwork(1003,17,'Visible',1,'local')))
        self.assertFalse(profile.allows(Artwork(1012,17,'Visible',8,'local')))

if __name__=='__main__':unittest.main()
