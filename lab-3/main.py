import asyncio
import json
import httpx

config = json.load(open('config.json'))
GRAPHHOPPER_API_KEY = config['GRAPHHOPPER_API_KEY']
GET_COORDS_URL = config['GET_COORDS_URL']
GET_INTERESTING_PLACES_URL = config['GET_INTERESTING_PLACES_URL']
GET_WEATHER_URL = config['GET_WEATHER_URL']
GET_INTERESTING_PLACES_RADIUS = config['GET_INTERESTING_PLACES_RADIUS']
GET_PLACE_DESCRIPTION_URL = config['GET_PLACE_DESCRIPTION_URL']
USER_AGENT = config['USER_AGENT']
GS_LIMIT = config["GS_LIMIT"]


async def search_locations(client: httpx.AsyncClient, query: str) -> list[dict]:
    params = {"q": query, "locale": "ru", "key": GRAPHHOPPER_API_KEY, "limit": 5}
    response = await client.get(GET_COORDS_URL, params=params)
    response.raise_for_status()
    return response.json()["hits"]


async def get_weather(client, coords):
    headers = {'User-Agent': USER_AGENT}
    params = {'lat': coords[0], 'lon': coords[1]}
    response = await client.get(GET_WEATHER_URL, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    details = response.json()["properties"]["timeseries"][0]["data"]["instant"]["details"]
    return {
        "temperature": details["air_temperature"],
        "humidity": details["relative_humidity"],
    }


async def get_interesting_places(client, coords):
    params = {
        "action": "query",
        "list": "geosearch",
        "gsradius": GET_INTERESTING_PLACES_RADIUS,
        "gscoord": '|'.join(map(str, coords)),
        "gslimit": GS_LIMIT,
        "format": "json",
    }
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    response = await client.get(GET_INTERESTING_PLACES_URL, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    data = response.json()
    if "query" not in data or "geosearch" not in data["query"]:
        return []
    return list(data["query"]["geosearch"])


async def get_place_description(client, pageid: int, max_chars: int = 300) -> str:
    params = {
        "action": "query",
        "prop": "extracts",
        "pageids": pageid,
        "exchars": max_chars,
        "exintro": True,
        "explaintext": True,
        "exsectionformat": "plain",
        "format": "json",
        "formatversion": 2,
    }
    headers = {"User-Agent": USER_AGENT}
    response = await client.get(GET_PLACE_DESCRIPTION_URL, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    pages = response.json()["query"]["pages"]
    if not pages or "extract" not in pages[0]:
        return "Описание отсутствует"
    return pages[0]["extract"]


async def explore_location(client: httpx.AsyncClient, coords: tuple[float, float]) -> dict:
    weather_task = asyncio.create_task(get_weather(client, coords))
    places = await get_interesting_places(client, coords)

    descriptions = await asyncio.gather(*(
        get_place_description(client, p["pageid"]) for p in places
    ))
    weather = await weather_task

    return {
        "weather": weather,
        "places": [
            {**place, "description": desc}
            for place, desc in zip(places, descriptions)
        ],
    }


async def main():
    async with httpx.AsyncClient() as client:
        query = (await asyncio.to_thread(input, "Введите название локации: ")).strip()
        if not query:
            return

        variants = await search_locations(client, query)
        if not variants:
            print("Ничего не найдено")
            return
        for i, v in enumerate(variants, 1):
            name = v.get("name", "без названия")
            kind = f"{v.get('osm_key', '')}:{v.get('osm_value', '')}".strip(":")
            print(f"{i}. {name} [{kind}] ({v['point']['lat']}, {v['point']['lng']})")

        idx = int((await asyncio.to_thread(input, "Выберите номер: ")).strip()) - 1
        chosen = variants[idx]
        coords = (chosen["point"]["lat"], chosen["point"]["lng"])

        result = await explore_location(client, coords)

        print(f"\n===== {chosen['name']} =====")
        weather = result["weather"]
        print(f"Погода: {weather['temperature']}°C, влажность {weather['humidity']}%")
        print()
        for place in result["places"]:
            print(f">{place['title']} - {int(place['dist'])}м ({place['lat']}, {place['lon']})")
            print(place["description"].rstrip(' '), end='\n\n')


if __name__ == '__main__':
    asyncio.run(main())