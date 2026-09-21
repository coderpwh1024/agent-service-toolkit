from pydantic import BaseModel


class ApiResponse[ResponseData](BaseModel):
    code: int
    message: str
    data: ResponseData | None = None


def api_success[ResponseData](
    data: ResponseData, message: str = "success"
) -> ApiResponse[ResponseData]:
    return ApiResponse(code=200, message=message, data=data)
