"""The documentation pages and the logo they show."""


def test_logo_is_served(client):
    for name in ("lucentLIMS-logo.svg", "lucentLIMS-icon.svg", "lucentLIMS-icon-monochrome.svg"):
        response = client.get(f"/static/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")


def test_docs_pages_use_the_icon(client):
    for path in ("/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200
        assert '/static/lucentLIMS-icon.svg' in response.text
        assert '/openapi.json' in response.text


def test_docs_follow_the_root_path(app):
    from fastapi.testclient import TestClient

    # As behind a proxy with `uvicorn --root-path /lucent`.
    page = TestClient(app, root_path="/lucent").get("/docs").text
    assert "url: '/lucent/openapi.json'" in page
    assert 'href="/lucent/static/lucentLIMS-icon.svg"' in page


def test_description_shows_the_logo(client):
    description = client.get("/openapi.json").json()["info"]["description"]
    assert description.lstrip().startswith('<img src="static/lucentLIMS-logo.svg"')
