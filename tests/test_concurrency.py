import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections, connection, connections
from django.test import Client

from portal.models import Claim, Draft, DraftAdmission, Job


@pytest.mark.django_db(transaction=True)
def test_racing_submissions_create_one_claim(prepared):
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock behavior requires PostgreSQL.")
    cookies = prepared["client"].cookies

    def submit():
        close_old_connections()
        try:
            client = Client()
            client.cookies = cookies.copy()
            return client.post(
                "/forms/test/submit/",
                data=json.dumps(prepared["data"]),
                content_type="application/json",
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert [result.status_code for result in results] == [202, 202]
    assert results[0].json() == results[1].json()
    assert Claim.objects.count() == 1
    assert Job.objects.filter(kind="render").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_fresh_sessions_share_one_creation_budget(event, settings):
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock behavior requires PostgreSQL.")
    settings.MAX_DRAFTS_PER_NETWORK = 1

    def open_form(_):
        close_old_connections()
        try:
            return Client().get("/forms/test/").status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(open_form, range(4)))
    assert sorted(statuses) == [200, 429, 429, 429]
    assert Draft.objects.count() == 1
    assert DraftAdmission.objects.get().count == 1
