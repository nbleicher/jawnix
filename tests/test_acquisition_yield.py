from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.api import app
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import Lead
from jawnix.acquisition_yield_api import membership_service_token


def test_membership_includes_db2_and_does_not_create_inventory(session, settings):
    settings.scraper_control_token = "fixture-service-token-0000000000000000"
    session.add(Lead(phone="6145550101",title="DB2 fixture",state="OH",in_db1=False))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        body = {"candidates":[
            {"phone":"+1 (614) 555-0101","title":"Existing","state":"OH"},
            {"phone":"6145550102","title":"New","state":"OH"},
            {"phone":"614-555-0102","title":"Duplicate format","state":"OH"},
            {"phone":"+44 1234567890","title":"Foreign","state":"OH"},
            {"phone":"6145550103","title":"","state":"OH"},
            {"phone":"6145550104","title":"Invalid state","state":"ON"},
        ]}
        assert client.post("/api/internal/acquisition/phone-membership",json=body).status_code==401
        response=client.post("/api/internal/acquisition/phone-membership",json=body,
                             headers={"Authorization":"Bearer "+membership_service_token(settings.scraper_control_token)})
        assert response.status_code==200
        assert response.json()=={"valid_phones":["6145550101","6145550102"],"existing_phones":["6145550101"]}
        assert session.scalar(select(func.count()).select_from(Lead))==1
    finally:
        app.dependency_overrides.clear()
