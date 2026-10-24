"""Bounded Actions log data transport; stdlib only, safe to load by file path."""
import os
import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'puae6-actions-log-reader-v1'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_job_log(repository, job_id):
    """Treat log bodies as bounded data; never forward API credentials to storage."""
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) is not None
            and type(job_id) is int and job_id > 0, 'invalid GitHub job descriptor')
    token = os.environ.get('GH_TOKEN', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', token) is not None, 'missing/invalid Actions read token')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs',
                                     headers={'Authorization': 'Bearer ' + token, 'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
    limit = 8 * 1024 * 1024
    for redirected in (False, True):
        try:
            with opener.open(request, timeout=30) as response:
                require(response.status == 200 and response.headers.get_content_type() in ('text/plain', 'application/octet-stream'), 'unexpected log response type/status')
                length = response.headers.get('Content-Length')
                require(length is None or (length.isdecimal() and int(length) <= limit), 'oversized/invalid log length')
                data = response.read(limit + 1)
                require(len(data) <= limit, 'oversized log body')
            text = data.decode('utf-8')
            text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]', '', text)
            return ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))
        except urllib.error.HTTPError as error:
            location = error.headers.get('Location', '')
            code = error.code; error.close()
            require(code == 302 and not redirected, 'GitHub log request failed')
            target = urllib.parse.urlsplit(location)
            require(len(location) <= 8192 and target.scheme == 'https' and target.hostname
                    and target.username is None and target.password is None and not any(ord(c) < 33 for c in location), 'unsafe log redirect')
            # A new request, not a redirected authenticated request. No token,
            # cookies or API headers are sent to the signed storage URL.
            request = urllib.request.Request(location, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
        except urllib.error.URLError as error:
            raise RuntimeError('GitHub log transport failed') from error


