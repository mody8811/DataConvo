"""Central SEO registry for Data Convo.

One place that knows every indexable URL, its title, its meta description and
its social-card copy. The sitemap route, the <head> partial and the JSON-LD
builder all read from here, so a new docs page or blog post can never again be
missing from sitemap.xml.

Indexable = a public page that should rank (marketing, docs, blog).
Non-indexable = auth screens and app surfaces.
"""
from __future__ import annotations

import json
import os
from datetime import date
from urllib.parse import urlparse

# Canonical host. Every sitemap URL, rel=canonical and og:url is built from
# this so apex -> www redirects never split the signal.
DEFAULT_SITE_URL = 'https://www.dataconvo.app'

BRAND = 'Data Convo'
DEFAULT_OG_IMAGE = '/static/brand/logo.png'
DEFAULT_OG_IMAGE_ALT = 'Data Convo — three studios for AI-ready data'


_LOCAL_HOSTS = ('localhost', '127.0.0.1', '0.0.0.0', '::1')


def site_url() -> str:
    """Canonical base URL with no trailing slash.

    SITE_URL normally comes from the environment. It is deliberately ignored
    when it points at a local host: a development .env reaching production
    would otherwise write http://localhost:5000 into every canonical tag and
    every sitemap URL, which de-indexes the real site.
    """
    configured = (os.getenv('SITE_URL') or '').strip().rstrip('/')
    if configured:
        try:
            host = urlparse(configured).hostname or ''
        except ValueError:
            host = ''
        if host and host.lower() not in _LOCAL_HOSTS:
            return configured
    return DEFAULT_SITE_URL


def absolute(path: str) -> str:
    """Join the canonical base with a site-relative path."""
    if not path.startswith('/'):
        path = '/' + path
    return site_url() + path


def normalize_path(path: str) -> str:
    """Strip query string and any trailing slash (except root)."""
    path = (path or '/').split('?')[0].split('#')[0]
    if len(path) > 1 and path.endswith('/'):
        path = path.rstrip('/')
    return path or '/'


# ---------------------------------------------------------------------------
# Static page registry
# ---------------------------------------------------------------------------
PAGES = {
    'home': dict(
        path='/',
        title='Data Convo — Semantic Layer for AI-Ready Databases',
        description=(
            'Self-hosted AI data workspace with 3 studios: a visual semantic layer, '
            'continuous anomaly detection, and interactive dashboards. Deploy on your terms.'
        ),
        priority='1.0', changefreq='weekly', indexable=True,
    ),
    'pricing': dict(
        path='/pricing',
        title='Pricing — Self-Hosted AI Data Workspace | Data Convo',
        description=(
            'Community, Team and Enterprise plans for a Docker-first semantic layer. '
            'Bring your own LLM keys and keep warehouse data inside your own VPC.'
        ),
        priority='0.9', changefreq='monthly', indexable=True,
    ),
    'demo': dict(
        path='/demo',
        title='Book a Demo — See Data Convo on Your Warehouse',
        description=(
            'Walk through the semantic layer, anomaly studio and dashboards on your own '
            'Snowflake, Databricks, BigQuery or Postgres schema. 30 minutes, no slides.'
        ),
        priority='0.8', changefreq='monthly', indexable=True,
    ),
    'docs': dict(
        path='/docs',
        title='Documentation — Connect, Model, Monitor | Data Convo',
        description=(
            'Setup guides for every supported warehouse plus step-by-step walkthroughs of '
            'the Semantic Layer Studio and Anomaly Studio.'
        ),
        priority='0.9', changefreq='weekly', indexable=True,
    ),
    'semantic_layer': dict(
        path='/semantic-layer',
        title='Enterprise Semantic Layer Without dbt or YAML | Data Convo',
        description=(
            'Model your warehouse visually, self-hosted and BYOK. Three studios in one '
            'workspace: a governed semantic layer, anomaly detection and live dashboards.'
        ),
        priority='0.9', changefreq='monthly', indexable=True,
    ),
    'blog': dict(
        path='/blog',
        title='Blog — Semantic Layers, Text-to-SQL and Data Quality',
        description=(
            'Product updates and engineering notes on making warehouse data usable by AI: '
            'semantic layers, governed text-to-SQL and anomaly detection.'
        ),
        priority='0.7', changefreq='weekly', indexable=True,
    ),

    # Auth screens: reachable, but nothing here should compete in search.
    'login': dict(
        path='/login',
        title='Sign In — Data Convo',
        description='Sign in to your Data Convo workspace.',
        priority='0.1', changefreq='yearly', indexable=False,
    ),
    'signup': dict(
        path='/signup',
        title='Create Your Account — Data Convo',
        description='Create a Data Convo account and deploy your own AI data workspace.',
        priority='0.1', changefreq='yearly', indexable=False,
    ),
    'forgot_password': dict(
        path='/forgot-password',
        title='Reset Your Password — Data Convo',
        description='Request a password reset link for your Data Convo account.',
        priority='0.1', changefreq='yearly', indexable=False,
    ),
}


# ---------------------------------------------------------------------------
# Docs registry (mirrors app/routes/docs.py SOURCES / WORKFLOWS)
# ---------------------------------------------------------------------------
DOCS_SOURCES = {
    'snowflake': dict(
        name='Snowflake',
        title='Connect Snowflake to Data Convo — Setup Guide',
        description=(
            'Connect Snowflake in minutes: account identifier, warehouse role and a '
            'read-only user. Full field list and connection string included.'
        ),
    ),
    'databricks': dict(
        name='Databricks',
        title='Connect Databricks to Data Convo — Setup Guide',
        description=(
            'Point Data Convo at a Databricks SQL warehouse: server hostname, HTTP path, '
            'catalog and a scoped personal access token.'
        ),
    ),
    'bigquery': dict(
        name='Google BigQuery',
        title='Connect BigQuery to Data Convo — Setup Guide',
        description=(
            'Grant a service account BigQuery Data Viewer plus Job User, then connect '
            'your project and dataset to Data Convo.'
        ),
    ),
    'redshift': dict(
        name='Amazon Redshift',
        title='Connect Amazon Redshift to Data Convo — Setup Guide',
        description=(
            'Connect an Amazon Redshift cluster to Data Convo using the psycopg2 driver: '
            'endpoint, port 5439, database and schema.'
        ),
    ),
    'postgresql': dict(
        name='PostgreSQL',
        title='Connect PostgreSQL to Data Convo — Setup Guide',
        description=(
            'Connect PostgreSQL with a read-only role: host, port, database, schema and '
            'the exact GRANT statement to run first.'
        ),
    ),
    'mysql': dict(
        name='MySQL',
        title='Connect MySQL to Data Convo — Setup Guide',
        description=(
            'Connect MySQL through PyMySQL, including the firewall allowlist step most '
            'hosted databases need before the first query.'
        ),
    ),
    'sqlserver': dict(
        name='Microsoft SQL Server',
        title='Connect SQL Server to Data Convo — Setup Guide',
        description=(
            'Connect Microsoft SQL Server with ODBC Driver 18: host instance, port 1433, '
            'database and dbo schema.'
        ),
    ),
    'sqlite': dict(
        name='SQLite',
        title='Connect SQLite to Data Convo — Setup Guide',
        description=(
            'Point Data Convo at a local .db file. No migration, no server, no extra '
            'driver: useful for demoing a semantic layer in minutes.'
        ),
    ),
}

DOCS_WORKFLOWS = {
    'semantic-layer': dict(
        name='Semantic Layer Studio',
        title='Semantic Layer Studio — Build an AI-Ready Model',
        description=(
            'Six-step walkthrough: connect, profile, model joins on the ERD canvas, define '
            'metrics and business definitions, publish, then chat.'
        ),
    ),
    'anomaly': dict(
        name='Anomaly Studio',
        title='Anomaly Studio — Continuous Data Quality Monitoring',
        description=(
            'Baseline every column, detect null spikes and volume shifts at three sigma, '
            'then route alerts to email or Slack.'
        ),
    ),
}


def docs_source_path(key: str) -> str:
    return f'/docs/sources/{key}'


def docs_workflow_path(key: str) -> str:
    return f'/docs/{key}'


# ---------------------------------------------------------------------------
# Lookup used by the <head> partial
# ---------------------------------------------------------------------------

def page_meta(path: str):
    """Return SEO metadata for a request path, or None if unmapped."""
    path = normalize_path(path)

    for key, page in PAGES.items():
        if page['path'] == path:
            return dict(page, key=key)

    if path.startswith('/docs/sources/'):
        key = path[len('/docs/sources/'):]
        src = DOCS_SOURCES.get(key)
        if src:
            return dict(key=f'docs-source-{key}', path=path, title=src['title'],
                        description=src['description'], priority='0.7',
                        changefreq='monthly', indexable=True)

    if path.startswith('/docs/'):
        key = path[len('/docs/'):]
        wf = DOCS_WORKFLOWS.get(key)
        if wf:
            return dict(key=f'docs-workflow-{key}', path=path, title=wf['title'],
                        description=wf['description'], priority='0.8',
                        changefreq='monthly', indexable=True)
    return None


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

def organization_jsonld():
    return {
        '@type': 'Organization',
        '@id': site_url() + '/#organization',
        'name': BRAND,
        'url': site_url() + '/',
        'logo': absolute(DEFAULT_OG_IMAGE),
        'email': 'support@dataconvo.app',
    }


def software_application_jsonld():
    """SoftwareApplication / SaaS node for the product pages."""
    return {
        '@type': 'SoftwareApplication',
        '@id': site_url() + '/#software',
        'name': BRAND,
        'applicationCategory': 'BusinessApplication',
        'applicationSubCategory': 'Business Intelligence',
        'operatingSystem': 'Docker, Linux, macOS, Windows',
        'description': (
            'Self-hosted AI data workspace with three studios: a visual semantic layer, '
            'continuous anomaly detection, and interactive natural-language dashboards.'
        ),
        'url': site_url() + '/',
        'publisher': {'@id': site_url() + '/#organization'},
        'featureList': [
            'Visual semantic layer with ERD join modeling',
            'Business definitions, custom metrics, enums and synonyms',
            'Governed text-to-SQL with a ReAct agent loop',
            'Continuous anomaly detection with email and Slack alerts',
            'Interactive dashboards generated from natural language',
            'Bring-your-own-key LLM providers',
            'Self-hosted deployment via Docker',
            'Row and column level role-based access control',
        ],
        'offers': [
            {'@type': 'Offer', 'name': 'Community', 'price': '0', 'priceCurrency': 'USD'},
            {'@type': 'Offer', 'name': 'Team / Pro', 'price': '399', 'priceCurrency': 'USD'},
        ],
    }


def website_jsonld():
    return {
        '@type': 'WebSite',
        '@id': site_url() + '/#website',
        'url': site_url() + '/',
        'name': BRAND,
        'publisher': {'@id': site_url() + '/#organization'},
    }


def breadcrumb_jsonld(crumbs):
    """crumbs: list of (name, path)."""
    return {
        '@type': 'BreadcrumbList',
        'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': name, 'item': absolute(path)}
            for i, (name, path) in enumerate(crumbs)
        ],
    }


FAQ_ITEMS = [
    (
        'What is an enterprise semantic layer?',
        'A semantic layer is a governed model of your business that sits between your '
        'warehouse and the tools that query it. It defines what each table, column and '
        'metric means in business language, so every answer is computed from the same '
        'definitions rather than re-derived by each analyst or language model.',
    ),
    (
        'How is this different from dbt or a YAML-based semantic layer?',
        'dbt and similar tools describe the semantic model in code: YAML files, a '
        'repository, a CI pipeline and a build step. Data Convo models the same concepts '
        'visually on an entity-relationship canvas and stores them as JSON attached to '
        'your live connection. There is no repository to maintain and no build to wait '
        'for: you connect a warehouse, define joins, metrics and definitions in the '
        'browser, and publish.',
    ),
    (
        'Does Data Convo send our warehouse data to a third party?',
        'No. Data Convo is deployed self-hosted via Docker inside your own environment '
        'and connects to your warehouse directly. You bring your own language model key '
        '(BYOK) and choose the provider, so the only outbound traffic is the schema '
        'context and the question you asked, sent to the provider you selected.',
    ),
    (
        'How does table-level security work for teams?',
        'Administrators grant access per table and per column for member accounts. The '
        'permission list is applied before any query is generated, so a restricted table '
        'is never included in the prompt or the generated SQL for that member. Role and '
        'column restrictions are enforced server-side, not hidden in the interface.',
    ),
    (
        'What happens when the underlying data changes unexpectedly?',
        'Anomaly Studio baselines each monitored column and flags deviations at roughly '
        'three standard deviations: null spikes, sudden volume changes and type '
        'mismatches. Alerts route to email or a Slack webhook as soon as a metric drifts, '
        'so a broken pipeline surfaces before it reaches a dashboard.',
    ),
    (
        'Can I ask questions about a chart I am already looking at?',
        'Yes. The Interactive Dashboarding Studio renders results as charts and KPI '
        'cards, and you can keep chatting about the visual that is on screen. Follow-up '
        'questions run against the same dataset, so you can pivot, filter or drill in '
        'without writing SQL.',
    ),
]


def _semantic_layer_faq_jsonld():
    """FAQPage node for the /semantic-layer landing page."""
    return {
        '@type': 'FAQPage',
        '@id': site_url() + '/semantic-layer#faq',
        'mainEntity': [
            {
                '@type': 'Question',
                'name': q,
                'acceptedAnswer': {'@type': 'Answer', 'text': a},
            }
            for q, a in FAQ_ITEMS
        ],
    }


def graph(nodes):
    return {'@context': 'https://schema.org', '@graph': [n for n in nodes if n]}


def _breadcrumbs_for(path: str):
    """Build a breadcrumb trail for docs, blog and marketing pages."""
    path = normalize_path(path)
    if not path.startswith(('/docs', '/blog', '/pricing', '/demo', '/semantic-layer')):
        return None
    crumbs = [('Home', '/')]
    if path == '/semantic-layer':
        crumbs.append(('Semantic Layer', '/semantic-layer'))
        return crumbs
    if path.startswith('/docs'):
        crumbs.append(('Docs', '/docs'))
        if path.startswith('/docs/sources/'):
            key = path[len('/docs/sources/'):]
            src = DOCS_SOURCES.get(key)
            if src:
                crumbs.append((src['name'], path))
        elif path != '/docs':
            key = path[len('/docs/'):]
            wf = DOCS_WORKFLOWS.get(key)
            if wf:
                crumbs.append((wf['name'], path))
    elif path.startswith('/blog'):
        crumbs.append(('Blog', '/blog'))
        if path != '/blog':
            crumbs.append(('Post', path))
    elif path == '/pricing':
        crumbs.append(('Pricing', '/pricing'))
    elif path == '/demo':
        crumbs.append(('Book a Demo', '/demo'))
    return crumbs if len(crumbs) > 1 else None


def jsonld_for(path: str):
    """Return the JSON-LD graph for a page.

    Organization + WebSite on every page, SoftwareApplication on the pages that
    sell the product, plus a BreadcrumbList where the page has a trail.
    """
    path = normalize_path(path)
    nodes = [organization_jsonld(), website_jsonld()]

    meta = page_meta(path)
    if meta and meta.get('indexable'):
        if path in ('/', '/pricing', '/demo', '/docs', '/semantic-layer'):
            nodes.append(software_application_jsonld())
        crumbs = _breadcrumbs_for(path)
        if crumbs:
            nodes.append(breadcrumb_jsonld(crumbs))
        if path == '/semantic-layer':
            nodes.append(_semantic_layer_faq_jsonld())

    return json.dumps(graph(nodes), ensure_ascii=False, separators=(',', ':'))


# ---------------------------------------------------------------------------
# Flask / Jinja wiring
# ---------------------------------------------------------------------------

def canonical_url(path=None):
    """Absolute canonical URL for a path, or for the current request."""
    if path is None:
        try:
            from flask import request
            path = request.path
        except Exception:
            path = '/'
    return absolute(normalize_path(path))


def init_app(app):
    """Register template helpers. Safe to call more than once."""
    from flask import request

    def _current_path():
        try:
            return normalize_path(request.path)
        except Exception:
            return '/'

    def meta_for(path=None):
        return page_meta(path if path is not None else _current_path())

    app.jinja_env.globals.update(
        seo_meta_for=meta_for,
        seo_canonical=canonical_url,
        seo_absolute=absolute,
        seo_jsonld=jsonld_for,
        seo_site_url=site_url,
        seo_brand=BRAND,
    )
    return app


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------

def sitemap_entries(blog_posts=None):
    """Every indexable URL with priority and changefreq.

    blog_posts: iterable of (slug, last_modified_date) from the blog model.
    """
    today = date.today().isoformat()
    entries = []

    for key, page in PAGES.items():
        if not page['indexable']:
            continue
        entries.append({
            'loc': absolute(page['path']),
            'lastmod': today,
            'changefreq': page['changefreq'],
            'priority': page['priority'],
        })

    for key in DOCS_SOURCES:
        entries.append({
            'loc': absolute(docs_source_path(key)),
            'lastmod': today,
            'changefreq': 'monthly',
            'priority': '0.7',
        })

    for key in DOCS_WORKFLOWS:
        entries.append({
            'loc': absolute(docs_workflow_path(key)),
            'lastmod': today,
            'changefreq': 'monthly',
            'priority': '0.8',
        })

    for slug, lastmod in (blog_posts or []):
        entries.append({
            'loc': absolute(f'/blog/{slug}'),
            'lastmod': (lastmod or today),
            'changefreq': 'yearly',
            'priority': '0.6',
        })

    return entries