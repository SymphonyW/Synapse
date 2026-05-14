import json
import re
import socket
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from app.tools.base import ToolCall, ToolContext, ToolResult


class _OpenAPIExecutorError(Exception):
    def __init__(
        self,
        message: str,
        code: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})


class _OpenAPIRedirectError(_OpenAPIExecutorError):
    pass


class _ValidatedRedirectHandler(urllib_request.HTTPRedirectHandler):
    def __init__(self, executor: "OpenAPIHTTPExecutor") -> None:
        self._executor = executor

    def redirect_request(
        self,
        req: urllib_request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib_request.Request | None:
        target_url = urllib_parse.urljoin(req.full_url, newurl)
        try:
            self._executor.validate_url(target_url)
        except _OpenAPIExecutorError as exc:
            raise _OpenAPIRedirectError(
                f"openapi redirect target is not allowed: {target_url}",
                code="openapi_redirect_blocked",
                details={
                    "redirect_status": code,
                    "target_url": target_url,
                    "reason": exc.code,
                },
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, target_url)


@dataclass(frozen=True)
class _PreparedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


class OpenAPIHTTPExecutor:
    """HTTP executor for OpenAPIToolProvider.

    Network behavior stays here instead of in runtime.py. Runtime still owns
    ToolPolicy, approval and ToolAuditLogger before this callable is reached.
    """

    def __init__(
        self,
        base_url_override: str = "",
        allowlist: tuple[str, ...] | list[str] | set[str] | None = None,
        timeout_seconds: float = 12.0,
        max_response_bytes: int = 65536,
        allowed_schemes: tuple[str, ...] | list[str] | set[str] | None = None,
        static_headers: dict[str, str] | None = None,
        bearer_token: str = "",
        api_key_header: str = "",
        api_key_value: str = "",
    ) -> None:
        self.base_url_override = base_url_override.strip()
        self.allowlist = tuple(
            item.strip().lower()
            for item in (allowlist or ())
            if str(item).strip()
        )
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_response_bytes = max(1, int(max_response_bytes))
        self.allowed_schemes = tuple(
            item.strip().lower()
            for item in (allowed_schemes or ("http", "https"))
            if str(item).strip()
        ) or ("http", "https")
        self.static_headers = {
            str(key).strip(): str(value)
            for key, value in (static_headers or {}).items()
            if str(key).strip()
        }
        self.bearer_token = bearer_token.strip()
        self.api_key_header = api_key_header.strip()
        self.api_key_value = api_key_value.strip()
        self._opener = urllib_request.build_opener(_ValidatedRedirectHandler(self))
        self._secret_values = tuple(
            value
            for value in (
                *self.static_headers.values(),
                self.bearer_token,
                self.api_key_value,
            )
            if value
        )

    def __call__(
        self,
        operation: dict[str, Any],
        call: ToolCall,
        context: ToolContext,
    ) -> ToolResult:
        _ = context
        try:
            prepared = self._prepare_request(operation, call)
            self.validate_url(prepared.url)
            request = urllib_request.Request(
                prepared.url,
                data=prepared.body,
                headers=prepared.headers,
                method=prepared.method,
            )
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status_code = int(getattr(response, "status", 0) or response.getcode())
                content_type = str(response.headers.get("Content-Type", "")).split(";")[0]
                raw_body = response.read(self.max_response_bytes + 1)
                return self._result_from_response(
                    raw_body=raw_body,
                    status_code=status_code,
                    content_type=content_type,
                    url=prepared.url,
                )
        except _OpenAPIExecutorError as exc:
            return ToolResult.failure(
                self._redact(str(exc)),
                code=exc.code,
                retryable=exc.retryable,
                details=self._redact_details(exc.details),
            )
        except urllib_error.HTTPError as exc:
            return self._http_error_result(exc)
        except (TimeoutError, socket.timeout) as exc:
            return ToolResult.failure(
                f"openapi request timed out after {self.timeout_seconds:g}s",
                code="openapi_timeout",
                retryable=True,
                details={"timeout_seconds": self.timeout_seconds},
            )
        except urllib_error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return ToolResult.failure(
                    f"openapi request timed out after {self.timeout_seconds:g}s",
                    code="openapi_timeout",
                    retryable=True,
                    details={"timeout_seconds": self.timeout_seconds},
                )
            return ToolResult.failure(
                self._redact(f"openapi request failed: {exc.reason}"),
                code="openapi_http_error",
                details={"reason": self._redact(str(exc.reason))},
            )
        except OSError as exc:
            return ToolResult.failure(
                self._redact(f"openapi request failed: {exc}"),
                code="openapi_http_error",
                details={"reason": self._redact(str(exc))},
            )

    def validate_url(self, url: str) -> None:
        parsed = urllib_parse.urlparse(url)
        scheme = parsed.scheme.strip().lower()
        host = (parsed.hostname or "").strip().lower()
        if scheme not in self.allowed_schemes:
            raise _OpenAPIExecutorError(
                f"openapi URL scheme {scheme or '<empty>'} is not allowed",
                code="openapi_invalid_arguments",
                details={"scheme": scheme, "allowed_schemes": list(self.allowed_schemes)},
            )
        if not host:
            raise _OpenAPIExecutorError(
                "openapi URL host is required",
                code="openapi_invalid_arguments",
                details={"url": url},
            )
        if not self._is_host_allowed(host):
            raise _OpenAPIExecutorError(
                f"openapi host {host} is not in allowlist",
                code="openapi_host_not_allowed",
                details={"host": host, "allowlist": list(self.allowlist)},
            )

    def _prepare_request(self, operation: dict[str, Any], call: ToolCall) -> _PreparedRequest:
        method = str(operation.get("method", "GET")).strip().upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise _OpenAPIExecutorError(
                f"openapi method {method or '<empty>'} is not supported",
                code="openapi_invalid_arguments",
                details={"method": method},
            )

        base_url = self.base_url_override or str(operation.get("base_url", "")).strip()
        path = str(operation.get("path", "")).strip()
        if not base_url or not path:
            raise _OpenAPIExecutorError(
                "openapi operation requires base_url and path",
                code="openapi_invalid_arguments",
                details={"has_base_url": bool(base_url), "has_path": bool(path)},
            )

        arguments = dict(call.arguments)
        parameters = operation.get("parameters", [])
        if not isinstance(parameters, list):
            parameters = []

        resolved_path = self._replace_path_params(path, parameters, arguments)
        url = self._join_url(base_url, resolved_path)
        query_items = self._query_items(parameters, arguments)
        if query_items:
            separator = "&" if urllib_parse.urlparse(url).query else "?"
            url = f"{url}{separator}{urllib_parse.urlencode(query_items, doseq=True)}"

        headers = dict(self.static_headers)
        headers.update(self._header_items(parameters, arguments))
        body = self._request_body(operation, arguments)
        if body is not None and not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/json"
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.api_key_header and self.api_key_value:
            headers[self.api_key_header] = self.api_key_value

        return _PreparedRequest(method=method, url=url, headers=headers, body=body)

    def _replace_path_params(
        self,
        path: str,
        parameters: list[Any],
        arguments: dict[str, Any],
    ) -> str:
        path_params = {
            str(parameter.get("name", "")).strip()
            for parameter in parameters
            if isinstance(parameter, dict) and str(parameter.get("in", "")).strip().lower() == "path"
        }

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in arguments or arguments[name] is None or arguments[name] == "":
                raise _OpenAPIExecutorError(
                    f"openapi path parameter {name} is required",
                    code="openapi_invalid_arguments",
                    details={"parameter": name, "in": "path"},
                )
            return urllib_parse.quote(str(arguments[name]), safe="")

        resolved = re.sub(r"\{([^{}]+)\}", replace, path)
        for name in path_params:
            if name and f"{{{name}}}" in resolved:
                raise _OpenAPIExecutorError(
                    f"openapi path parameter {name} was not resolved",
                    code="openapi_invalid_arguments",
                    details={"parameter": name, "in": "path"},
                )
        return resolved

    def _query_items(self, parameters: list[Any], arguments: dict[str, Any]) -> list[tuple[str, Any]]:
        query_items: list[tuple[str, Any]] = []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if str(parameter.get("in", "")).strip().lower() != "query":
                continue
            name = str(parameter.get("name", "")).strip()
            if not name:
                continue
            if name not in arguments or arguments[name] is None or arguments[name] == "":
                if bool(parameter.get("required", False)):
                    raise _OpenAPIExecutorError(
                        f"openapi query parameter {name} is required",
                        code="openapi_invalid_arguments",
                        details={"parameter": name, "in": "query"},
                    )
                continue
            query_items.append((name, self._stringify_argument(arguments[name])))
        return query_items

    def _header_items(self, parameters: list[Any], arguments: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if str(parameter.get("in", "")).strip().lower() != "header":
                continue
            name = str(parameter.get("name", "")).strip()
            if not name:
                continue
            if name not in arguments or arguments[name] is None or arguments[name] == "":
                if bool(parameter.get("required", False)):
                    raise _OpenAPIExecutorError(
                        f"openapi header parameter {name} is required",
                        code="openapi_invalid_arguments",
                        details={"parameter": name, "in": "header"},
                    )
                continue
            headers[name] = str(arguments[name])
        return headers

    def _request_body(self, operation: dict[str, Any], arguments: dict[str, Any]) -> bytes | None:
        if "body" not in arguments:
            if bool(operation.get("request_body_required", False)):
                raise _OpenAPIExecutorError(
                    "openapi JSON request body is required",
                    code="openapi_invalid_arguments",
                    details={"parameter": "body"},
                )
            return None

        try:
            return json.dumps(arguments["body"], ensure_ascii=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise _OpenAPIExecutorError(
                f"openapi JSON request body is not serializable: {exc}",
                code="openapi_invalid_arguments",
                details={"parameter": "body"},
            ) from exc

    def _result_from_response(
        self,
        raw_body: bytes,
        status_code: int,
        content_type: str,
        url: str,
    ) -> ToolResult:
        if len(raw_body) > self.max_response_bytes:
            preview = self._decode_body(raw_body[: self.max_response_bytes], content_type)
            return ToolResult.failure(
                self._redact(f"openapi response too large; truncated preview: {preview}"),
                code="openapi_response_too_large",
                details={
                    "status_code": status_code,
                    "content_type": content_type,
                    "max_response_bytes": self.max_response_bytes,
                    "truncated": True,
                    "url": url,
                },
            )

        payload = self._decode_body(raw_body, content_type)
        return ToolResult.success(
            self._redact(f"openapi {status_code} response: {payload}"),
            metadata={
                "status_code": status_code,
                "content_type": content_type,
                "url": url,
                "truncated": False,
            },
        )

    def _http_error_result(self, exc: urllib_error.HTTPError) -> ToolResult:
        raw_body = exc.read(self.max_response_bytes + 1)
        content_type = str(exc.headers.get("Content-Type", "")).split(";")[0]
        truncated = len(raw_body) > self.max_response_bytes
        preview = self._decode_body(raw_body[: self.max_response_bytes], content_type)
        output = f"openapi HTTP {exc.code} response: {preview}"
        if truncated:
            output = f"{output} (truncated)"
        return ToolResult.failure(
            self._redact(output),
            code="openapi_http_error",
            retryable=500 <= int(exc.code) <= 599,
            details={
                "status_code": int(exc.code),
                "content_type": content_type,
                "truncated": truncated,
                "url": exc.url,
            },
        )

    def _decode_body(self, raw_body: bytes, content_type: str) -> str:
        decoded = raw_body.decode("utf-8", errors="replace")
        if "json" not in content_type.lower():
            return decoded
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
        return json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))

    def _join_url(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _stringify_argument(self, value: Any) -> Any:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return [self._stringify_argument(item) for item in value]
        return value

    def _is_host_allowed(self, host: str) -> bool:
        if not self.allowlist:
            return False
        for allowed in self.allowlist:
            if host == allowed:
                return True
            if self._can_use_suffix_match(host, allowed) and host.endswith("." + allowed):
                return True
        return False

    def _can_use_suffix_match(self, host: str, allowed: str) -> bool:
        if host == "localhost" or allowed == "localhost":
            return False
        try:
            ip_address(host)
            return False
        except ValueError:
            pass
        try:
            ip_address(allowed)
            return False
        except ValueError:
            return True

    def _redact_details(self, details: dict[str, Any]) -> dict[str, Any]:
        return {
            key: self._redact(value) if isinstance(value, str) else value
            for key, value in details.items()
        }

    def _redact(self, value: str) -> str:
        redacted = value
        for secret in self._secret_values:
            redacted = redacted.replace(secret, "[redacted]")
        return redacted
