"""Consistent JSON error envelope for the API."""
from flask import jsonify
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException


def error_response(code: str, message: str, status: int = 400, details=None):
    body = {'error': {'code': code, 'message': message}}
    if details is not None:
        body['error']['details'] = details
    response = jsonify(body)
    response.status_code = status
    return response


def register_error_handlers(app):
    @app.errorhandler(ValidationError)
    def _validation_error(err: ValidationError):
        # pydantic puts the originating exception in `ctx`, which isn't JSON-serializable.
        try:
            details = err.errors(include_context=False, include_url=False)
        except TypeError:
            details = [{k: v for k, v in e.items() if k != 'ctx'} for e in err.errors()]
        return error_response(
            'validation_error',
            'Request did not validate.',
            status=422,
            details=details,
        )

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        return error_response(
            err.name.lower().replace(' ', '_'),
            err.description or err.name,
            status=err.code or 500,
        )

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):
        if app.debug:
            raise err
        return error_response('internal_error', 'An unexpected error occurred.', status=500)
