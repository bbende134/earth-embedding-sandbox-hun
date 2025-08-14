from geojson_pydantic import Polygon


def test_geojson_api_success(client):
    """
    Test the GeoJSON API endpoint.
    """
    response = client.post(
        "/neighbours",
        json={
            "geojson": Polygon(
                type="Polygon",
                coordinates=[
                    [
                        [-3.0703271522479203, 51.74499803967353],
                        [-3.0840600624041703, 51.73011550863597],
                        [-3.0651773109393265, 51.70416566653665],
                        [-3.0500711097674515, 51.69480305315406],
                        [-3.0291284217791703, 51.702250744122296],
                        [-3.0308450355487015, 51.71522807583445],
                        [-3.0528176917987015, 51.720332890745894],
                        [-3.0541909828143265, 51.74606089005339],
                        [-3.0703271522479203, 51.74499803967353],
                    ]
                ],
            ).__geo_interface__,
            "k": 5,
        },
    )
    response.raise_for_status()  # Ensure we get a 2xx response
    assert response.status_code == 200
    data = response.json()
    assert "neighbours" in data
