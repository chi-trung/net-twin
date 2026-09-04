# net-twin backend

FastAPI service implementing the digital-twin loop: discovery → twin store → events → realtime API.

## Run (development)

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,net]"
uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Health:   http://localhost:8000/healthz

## Tests

```bash
pytest
```
