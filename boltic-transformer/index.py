import io
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from transformer import transform

app = FastAPI(title="Cottonworld Transformer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Warnings", "Content-Disposition"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transform")
async def transform_file(file: UploadFile = File(...)):
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are supported")

    contents = await file.read()
    try:
        buf, warnings, _ = transform(io.BytesIO(contents))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transform failed: {str(e)}")

    # HTTP headers are Latin-1; sanitize Unicode chars (em-dashes, arrows, etc.)
    warnings_safe = "||".join(warnings).encode("latin-1", errors="replace").decode("latin-1")
    headers = {
        "X-Warnings": warnings_safe,
        "Content-Disposition": 'attachment; filename="fynd_catalog_output.xlsx"',
    }
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
