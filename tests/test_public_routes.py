"""Regression tests: public marketing page vs auth-protected app routes.

Locks in the routing contract:
  GET  /semantic-layer         -> 200 for anonymous visitors (public landing page)
  GET  /semantic-studio        -> redirect to /login for anonymous visitors
  POST /publish-semantic-layer -> redirect to /login for anonymous visitors
  /semantic-layer renders the marketing template, never the studio UI
  /semantic-layer is in sitemap.xml; /semantic-studio never is
  robots.txt leaves the public path crawlable

Run:  python tests/test_public_routes.py
      (pytest tests/test_public_routes.py also works)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402

PUBLIC_PATH = '/semantic-layer'
STUDIO_PATH = '/semantic-studio'
PUBLISH_PATH = '/publish-semantic-layer'
PUBLIC_URL = 'https://www.dataconvo.app/semantic-layer'
AUTH_CODES = (302, 401, 403)


def make_client():
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app, app.test_client()


def robots_allows(robots, path):
    """Standard robots.txt longest-match-wins evaluation."""
    best_len, verdict = -1, True
    for line in robots.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith('allow:'):
            pat, allow = line.split(':', 1)[1].strip(), True
        elif low.startswith('disallow:'):
            pat, allow = line.split(':', 1)[1].strip(), False
        else:
            continue
        if pat and path.startswith(pat):
            if len(pat) > best_len or (len(pat) == best_len and allow):
                best_len, verdict = len(pat), allow
    return verdict


def run():
    app, client = make_client()
    checks = []

    # 1. public landing page is anonymous
    r = client.get(PUBLIC_PATH)
    checks.append(('public page returns 200 for anonymous', r.status_code == 200))

    body = r.get_data(as_text=True)
    checks.append(('renders marketing copy',
                   'Interactive Dashboarding Studio' in body
                   and 'Enterprise Semantic Layer Without dbt' in body))
    checks.append(('does not leak studio UI',
                   'studio_error' not in body))

    # 2. studio stays protected
    r = client.get(STUDIO_PATH)
    checks.append(('studio requires auth', r.status_code in AUTH_CODES))
    checks.append(('studio redirects to login',
                   r.status_code != 302 or '/login' in r.headers.get('Location', '')))

    # 3. publish stays protected
    r = client.post(PUBLISH_PATH, data={})
    checks.append(('publish requires auth', r.status_code in (302, 400) + AUTH_CODES))

    # 4. routes are distinct endpoints
    rules = {str(rule.rule): rule.endpoint for rule in app.url_map.iter_rules()}
    checks.append(('both routes registered',
                   PUBLIC_PATH in rules and STUDIO_PATH in rules))
    checks.append(('public and studio are different endpoints',
                   rules.get(PUBLIC_PATH) != rules.get(STUDIO_PATH)))

    # 5. sitemap: public in, private out
    locs = re.findall(r'<loc>([^<]+)</loc>',
                      client.get('/sitemap.xml').get_data(as_text=True))
    checks.append(('public page in sitemap', PUBLIC_URL in locs))
    checks.append(('studio excluded from sitemap',
                   not any('semantic-studio' in u for u in locs)))

    # 6. robots.txt does not block the public page
    robots = client.get('/robots.txt').get_data(as_text=True)
    checks.append(('robots allows public page', robots_allows(robots, PUBLIC_PATH)))
    checks.append(('robots blocks studio', not robots_allows(robots, STUDIO_PATH)))

    failed = 0
    for name, ok in checks:
        print('%-46s %s' % (name, 'PASS' if ok else 'FAIL'))
        failed += 0 if ok else 1

    print('')
    print('%d/%d passed' % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


# pytest entry points
def test_public_route_contract():
    assert run() == 0


if __name__ == '__main__':
    sys.exit(run())