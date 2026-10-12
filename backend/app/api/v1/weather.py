from fastapi import APIRouter, HTTPException, Query, Response
from app.services.weather import COUNTIES, get_forecast

router = APIRouter(prefix='/weather', tags=['weather'])


@router.get('/counties')
def counties():
    return COUNTIES


@router.get('/forecast')
def forecast(response: Response, county: str = Query(max_length=12)):
    response.headers['Cache-Control'] = 'no-store'
    if county not in COUNTIES:
        raise HTTPException(422, '請選擇臺灣縣市')
    return get_forecast(county)
