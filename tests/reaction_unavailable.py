"""Unavailable image reads stay optional only for valid image.get batches; writes never replay."""
import test_safety
import io
import json
import unittest
import urllib.error
from unittest.mock import patch, Mock
from discovery import social
import server


def missing(status=404, code='NOT_FOUND', path='image.get'):
    return {'error': {'json': {'message': 'unavailable', 'data': {
        'httpStatus': status, 'code': code, 'path': path}}}}


class UnavailableTests(unittest.TestCase):
    def batch(self, body, status=404, optional=True, procedure='image.get'):
        def transport(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, status, 'synthetic', {},
                                         io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode()))
        with patch.object(social, 'get_access_token', return_value='synthetic'), \
             patch.object(social, 'refresh_access_token', return_value='synthetic'), \
             patch.object(social.time, 'sleep'), patch.object(social.urllib.request, 'urlopen', side_effect=transport):
            client = social.SocialClient()
            return (client.batch_query_optional if optional else client.batch_query)(procedure, [{'id': 1}, {'id': 2}])

    def test_all_missing_http_batch(self):
        self.assertEqual(self.batch([missing(), missing()]), [None, None])

    def test_strict_or_other_procedure_still_fails(self):
        for kwargs in ({'optional': False}, {'procedure': 'user.getCreator'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(social.CivitaiHTTPError):
                self.batch([missing(), missing()], **kwargs)

    def test_invalid_or_incomplete_404_fails(self):
        for body in (b'not json', {}, [], [missing()], [None, None],
                     [missing(), missing(401, 'UNAUTHORIZED')],
                     [missing(), missing(path='unknown.procedure')]):
            with self.subTest(body=body), self.assertRaises((social.CivitaiHTTPError, RuntimeError)):
                self.batch(body)

    def test_auth_rate_service_failures_preserved(self):
        for status in (401, 403, 429, 500, 503):
            with self.subTest(status=status), self.assertRaises(social.CivitaiHTTPError) as caught:
                self.batch([missing(), missing()], status=status)
            self.assertEqual(caught.exception.status, status)

    def test_mixed_batch_preserves_available_state(self):
        with patch.object(social.SocialClient, '_request', return_value=[
            missing(), {'result': {'data': {'json': {'id': 2, 'reactions': ['Like']}}}}]):
            self.assertEqual(social.SocialClient().batch_query_optional('image.get', [{'id': 1}, {'id': 2}]),
                             [None, {'id': 2, 'reactions': ['Like']}])

    def test_failed_preflight_never_toggles_or_updates_stores(self):
        handler = object.__new__(server.Handler); handler.json_response = Mock()
        client = Mock(); store = Mock()
        for status in (404, 401, 403, 429, 503):
            client.query.side_effect = social.CivitaiHTTPError(status, 'synthetic')
            with self.subTest(status=status), patch.object(server, 'SocialClient', return_value=client), \
                 patch.object(server.CACHE, 'load', return_value={'items': []}), \
                 patch.object(server, 'image_stores', return_value=[store]):
                if status == 404:
                    handler.handle_reaction({'imageId': 1, 'reaction': 'Like', 'active': True})
                    self.assertEqual(handler.json_response.call_args.args[0]['code'], 'ARTWORK_UNAVAILABLE')
                else:
                    with self.assertRaises(social.CivitaiHTTPError):
                        handler.handle_reaction({'imageId': 1, 'reaction': 'Like', 'active': True})
                client.mutate.assert_not_called(); store.update_stats.assert_not_called()


if __name__ == '__main__':
    unittest.main()
