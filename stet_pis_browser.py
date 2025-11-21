"""Payment browser definitions for STET."""

from __future__ import annotations

import re
from base64 import b64encode
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from time import mktime
from typing import TYPE_CHECKING, Any, ClassVar, Iterator
from uuid import uuid4
from wsgiref.handlers import format_date_time

from dateutil.parser import parse as parse_date
from pydantic import TypeAdapter

from woob.browser.browsers import DigestMixin
from woob.browser.exceptions import ClientError, HTTPNotFound
from woob.browser.url import BrowserParamURL
from woob.capabilities.payment import (
    OneTimePaymentInstruction,
    OneTimePaymentInstructionStatus,
    OneTimePaymentInstructionStatusReason,
    PaymentAccessExpired,
    PaymentAccount,
    PaymentCancellationError,
    PaymentCancellationErrorCode,
    PaymentCancellationReason,
    PaymentConfirmationRequired,
    PaymentInteractionSkipped,
    PaymentRedirect,
    PaymentSameInteraction,
    PaymentValidationError,
    PaymentValidationErrorCode,
)
from woob.exceptions import BrowserInteraction, BrowserUnavailable
from woob.tools.capabilities.payment import (
    PaymentBrowser,
    build_end_to_end_identifiers,
)
from woob.tools.date import now_as_utc
from woob.tools.json import WoobEncoder, json
from woob.tools.pkce import PKCEChallengeType, PKCEData
from woob.tools.url import get_url_param, get_url_params, get_url_with_params

from .dialects import (
    Stet141PaymentDialect,
    Stet142PaymentDialect,
    Stet150PaymentDialect,
    Stet151PaymentDialect,
    Stet162PaymentDialect,
    Stet163PaymentDialect,
    StetPaymentDialect,
)
from .pages import (
    ErrorPage,
    NewPaymentPage,
    OAuthTokenPage,
    PaymentCancellationPage,
    PaymentPage,
)
from .state import PaymentBrowserState
from .utils import (
    OAuthClientAuthMethod,
    OAuthRedirectFlowConfirmationFactor,
    PaymentStatusData,
    PreStepType,
    RedirectFlow,
    StetException,
    ValidationApproach,
    build_random_identifier,
)

if TYPE_CHECKING:
    from requests.models import PreparedRequest, Request, Response

IPv4_RE = re.compile(
    r"([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])"
    + r"(\.([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])){3}",
)


class Stet140PaymentBrowser(DigestMixin, PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.4 of the STET PSD2 API
    standard, published on 2018-09-13 by Hervé Robache.
    """

    BASEURL: str | None = None
    """The base URL for the API."""

    ALLOW_REFERRER: ClassVar[bool] = False
    """There is no need for a 'Referer' header, we're an API browser."""

    VERSION = "1.4.0"
    """The version of the STET PSD2 API standard implemented by the browser."""

    # ---
    # Configuration for child browsers.
    # ---

    PROXYNET_SIGNATURE_TYPE: ClassVar[str] = "stet"
    """The signature type for requests in Proxynet.

    This will be set through the 'Proxynet-Signature-Type' session header.
    """

    ACCEPT_HAL_JSON: ClassVar[bool] = True
    """Whether to set the 'Accept' header to 'application/hal+json' or not.

    The STET PSD2 API standard dictates that payment routes must return a body
    with the 'application/hal+json' content type, implying that we are supposed
    to set our HTTP 'Accept' header accordingly.

    However, some ASPSPs actually expect the non-standard 'application/json'
    content type in the 'Accept' header, such as LCL's PIS API, that crashes
    with the following error:

    .. code-block:: json

        {
            "error": "Not Acceptable",
            "message": "Could not find acceptable representation",
            ...
        }

    Setting this constant to ``False`` sets the 'Accept' header to
    'application/json' instead of 'application/hal+json'.
    """

    DIALECT: ClassVar[type[StetPaymentDialect]] = Stet141PaymentDialect
    """The dialect to use when creating and checking payment requests."""

    OAUTH_TOKEN_URL: ClassVar[str | None] = None
    """The OAuth token URL.

    Note that if defined, this class variable gets formatted using
    ``str.format``, then set as the pattern for the :py:attr:`oauth_token_page`
    URL.
    """

    OAUTH_CLIENT_AUTH_METHOD: ClassVar[OAuthClientAuthMethod] = OAuthClientAuthMethod.POST
    """The method to authenticate an OAuth2 client on the token endpoint."""

    OAUTH_TOKEN_TYPE_ALIASES: dict[str, str] = {
        "bearer": "Bearer",
    }
    """Aliases for obtained token types.

    In case the ASPSP manages to be wrong in the token type it returns, we
    want to be able to adopt the right token type for later calls.
    """

    PRE_STEP_TYPE: ClassVar[PreStepType] = PreStepType.OAUTH_CLIENT
    """Pre-step type."""

    PRE_STEP_OAUTH_SCOPE: ClassVar[str] = "pisp"
    """Scope to use in case of an OAuth2 auth pre-step."""

    UNSAFE_CONFIRMATIONS: ClassVar[bool] = False
    """Whether unsafe confirmations are enabled or not.

    Unsafe confirmations are the ones that mix PSU and client confirmation
    in a single request, and have an element provided by the PSU supposed to
    prove that they have validated the payment.

    Such examples include:

    * Non-OAuth2 redirect flows, with or without an explicit confirmation,
      since such a confirmation mixes TPP and PSU validations;
    * OAuth2 redirect flows without an explicit confirmation.
    """

    UNSAFE_URL_ERROR_DETECTION: ClassVar[bool] = False
    """Whether to enable error detection from the URLs.

    This is a risk, since callback URLs can be constructed by the end user,
    or by our own errback for that matter, i.e. not by the bank.
    """

    VALIDATION_REDIRECT_FLOW: ClassVar[RedirectFlow] = RedirectFlow.SIMPLE
    """The type of redirect flow, if the API selects the REDIRECT approach."""

    VALIDATION_OAUTH_CONFIRMATION_FACTOR: ClassVar[OAuthRedirectFlowConfirmationFactor] = (
        OAuthRedirectFlowConfirmationFactor.NONE
    )
    """The auth factor to pass at confirmation for the OAuth redirect flow."""

    VALIDATION_REDIRECT_WITH_PKCE: ClassVar[bool] = False
    """Whether to include PKCE challenge data or not with redirect."""

    VALIDATION_PKCE_CHALLENGE_TYPE: ClassVar[PKCEChallengeType] = PKCEChallengeType.S256
    """The challenge type to use when generating PKCE."""

    VALIDATION_REDIRECT_URLS_REQUIRED: ClassVar[bool] = True
    """Whether to systematically add the redirect URLs in the payload.

    If not, and the validation redirect flow starts with an OAuth2
    Authorization Code grant, we do not include the successful and
    unsuccessful report URLs in the payment request creation payload.
    """

    VALIDATION_MAY_HAVE_AUTOMATIC_CONFIRMATION: ClassVar[bool] = False
    """Whether validation flows with confirmation may have been skipped.

    In most APIs with a validation flow including confirmation, encountering
    the 'ACCP' or 'ACSP' status before having confirmed means that confirmation
    is required, not skipped. As such, encountering this status before having
    confirmed leads to a ``PaymentConfirmationRequired`` being raised on
    our end.

    However, with certain APIs, such as BPCE implementing STET v1.6.2 with an
    OAuth2 Authorization Code flow with confirmation, an 'ACCP' or 'ACSP'
    status can be encountered without having confirmed the payment on our side,
    and in such cases, according to their support, we must consider the
    payment as automatically confirmed.

    Passing this browser constant to ``True`` makes us consider 'ACCP' and
    'ACSP' statuses obtained before having confirmed as "validation finished"
    instead of "confirmation required".
    """

    VALIDATION_CONFIRMATION_STATUS_REQUIRED: ClassVar[bool] = True
    """Whether to wait for a payment status explicitely requiring confirmation.

    This is mostly useful when we know the API will be in one of these
    situations:

    * The redirect flow is one that requires confirmation, and the API raises
      an 'ACCP' or 'ACSP' status once PSU validation has been completed, but
      confirmation is still required.
    * The payment status is set to 'ACCO' explicitely by the API (STET 1.6.2+
      specific status) once a confirmation is expected.

    If this attribute is set to :py:data:`False`, the callback will be
    considered as powerful a signal as one of these statuses, if returned,
    and confirmation will be attempted at regardless of the payment status.
    """

    VALIDATION_COMMON_OAUTH_CONFIRMATION: ClassVar[bool] = False
    """Whether OAuth2 validation redirect flows use the STET 1.5.0+ style.

    Pre-STET 1.5.0, the OAuth2 Authorisation Code redirect flow for payment
    validation uses the dedicated '/o-confirmation' endpoint, whereas in later
    versions of the standard, it uses the common '/confirmation' endpoint with
    no PSU authentication factor, only a newly obtained Bearer token.

    Setting this constant to true makes OAuth2 Authorisation Code redirect
    flows for payment validation use the STET 1.5.0+ confirmation style.
    """

    VALIDATION_FAILURE_CALLBACK_DETECTION_TIMEOUT: ClassVar[timedelta | None] = timedelta(minutes=5)
    """Timeout for reporting validation failures without callback.

    If the current validation approach is a redirect approach, and we detect
    that a payment is rejected during a background check on the payment,
    we may want to wait for a callback from the PSU before reporting the
    payment as failed, so that we can extract a more specific validation
    error code from the callback.

    If we detect the payment validation as failed and the timeout has been
    exceeded, we report the payment as failed with a generic code based on
    the API status.

    Note that this is only active if :py:attr:`UNSAFE_URL_ERROR_DETECTION`
    is enabled. If we need no delay for validation failure callback
    detection, this attribute should be set to :py:data:`None`.
    """

    CANCELLATION_STATUS: ClassVar[str] = "RJCT"
    """The status to define the payment request to, to cancel."""

    CANCELLATION_ON_INSTRUCTIONS: ClassVar[bool] = False
    """Whether the 'CANC' status should be put on instructions as well."""

    CANCELLATION_INCLUDE_APPLIED_APPROACH: ClassVar[bool] = False
    """Whether to include the validation approach in cancellation payload.

    Some ASPSPs such as La Banque Postale requires the payment's current
    'supplementaryData/appliedAuthenticationApproach' to be included within
    the cancellation payload.

    Setting this to :py:data:`True` will make the cancellation method gather
    the applied authentication approach from the payment to place it in the
    cancellation payload's supplementary data.
    """

    CANCELLATION_REDIRECT_FLOW: ClassVar[RedirectFlow] = RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION
    """The type of redirect flow for cancellation.

    This is only used if the API selects the REDIRECT approach.
    """

    CANCELLATION_OAUTH_CONFIRMATION_FACTOR: ClassVar[OAuthRedirectFlowConfirmationFactor] = (
        OAuthRedirectFlowConfirmationFactor.NONE
    )
    """The auth factor to pass at confirmation for the OAuth redirect flow."""

    CANCELLATION_REDIRECT_WITH_PKCE: ClassVar[bool] = False
    """Whether to include PKCE challenge data or not with redirect."""

    CANCELLATION_PKCE_CHALLENGE_TYPE: ClassVar[PKCEChallengeType] = PKCEChallengeType.S256
    """The challenge type to use when generating PKCE."""

    CANCELLATION_REDIRECT_URLS_REQUIRED: ClassVar[bool] = True
    """Whether to systematically add the redirect URLs in the canc. payload.

    If not, and the cancellation redirect flow starts with an OAuth2
    Authorization Code grant, we do not include the successful and
    unsuccessful report URLs in the payment request creation payload.
    """

    CANCELLATION_COMMON_OAUTH_CONFIRMATION: ClassVar[bool] = False
    """Whether OAuth2 cancellation redirect flows use the STET 1.5.0+ style.

    This attribute is the equivalent of
    :py:attr:`VALIDATION_COMMON_OAUTH_CONFIRMATION` for cancellation flows.
    """

    CANCELLATION_FAILURE_CALLBACK_DETECTION_TIMEOUT: ClassVar[timedelta | None] = timedelta(
        minutes=5
    )
    """Timeout for reporting cancellation validation failures without callback.

    Basically the equivalent of
    :py:attr:`VALIDATION_FAILURE_CALLBACK_DETECTION_TIMEOUT` for
    cancellation validation.
    """

    CHARGE_BEARER: ClassVar[str | None] = "SLEV"
    """The charge bearer mode to use when creating the payments.

    If defined to None, this property will not be added to the payment request
    creation payload.
    """

    END_TO_END_IDENTIFIERS_SUPPORTED: ClassVar[bool] = True
    """Whether end-to-end identifiers are supported by the API or not.

    If they are, end-to-end identifiers will be set to the payment
    instructions if necessary, and the end-to-end identifiers will be sent to
    the API.
    """

    INCLUDE_PSU_HEADERS: ClassVar[bool] = True
    """Whether to include 'PSU-*' headers to requests if available.

    Some ASPSPs deny requests if they are provided with such headers, so
    this variable allows not including them in requests.
    """

    RAISE_PAYMENTACCESSEXPIRED_ON_404: ClassVar[bool] = False
    """Whether to raise PaymentAccessExpired on 404 errors.

    Some ASPSPs may return temporary 404 errors during refresh payment,
    keep False to avoid losing payment access if it happens.
    """

    # ---
    # Code mappings.
    # ---

    VALIDATION_ERROR_CODE_MAPPING: ClassVar[dict[str, tuple[PaymentValidationErrorCode, str]],] = {
        "AC01": (
            PaymentValidationErrorCode.INVALID_PAYER,
            "The provided payer account is either invalid or does not exist.",
        ),
        "AC04": (
            PaymentValidationErrorCode.INVALID_PAYER,
            "The provided payer account is closed.",
        ),
        "AC06": (
            PaymentValidationErrorCode.INVALID_PAYER,
            "The provided payer account is blocked.",
        ),
        "AG01": (
            PaymentValidationErrorCode.INVALID_PAYER,
            "The payment is forbidden on this type of account.",
        ),
        "AM18": (
            PaymentValidationErrorCode.OTHER,
            "The number of instructions exceeds the bank acceptance limit.",
        ),
        "CH03": (
            PaymentValidationErrorCode.INVALID_DATE,
            "The requested execution date is too far in the future.",
        ),
        "CUST": (
            PaymentValidationErrorCode.CANCELLED,
            "The payer has cancelled the payment.",
        ),
        "FF01": (
            PaymentValidationErrorCode.OTHER,
            "The communication with the bank failed unexpectedly.",
        ),
        "FRAD": (
            PaymentValidationErrorCode.REGULATORY_REASON,
            "The payment was rejected for being detected as fraudulent.",
        ),
        "DS02": (
            PaymentValidationErrorCode.CANCELLED,
            "The payer has cancelled the payment.",
        ),
        "MS03": (
            PaymentValidationErrorCode.NONE,
            "The payment was denied by the bank.",
        ),
        "NOAS": (
            PaymentValidationErrorCode.EXPIRED,
            "The payer has neither accepted nor rejected the payment request.",
        ),
        "RR01": (
            PaymentValidationErrorCode.INVALID_PAYER,
            "The payer could not identify themselves.",
        ),
        "RR03": (
            PaymentValidationErrorCode.INVALID_BENEFICIARY,
            "Missing information about the beneficiary name and/or address.",
        ),
        "RR04": (
            PaymentValidationErrorCode.REGULATORY_REASON,
            "The payment has been rejected for a regulatory reason.",
        ),
    }

    CANCELLATION_REASON_MAPPING: ClassVar[dict[PaymentCancellationReason, str],] = {
        PaymentCancellationReason.ORDERED_BY_PSU: "DS02",
    }
    """Cancellation reason mapping.

    If a cancellation reason is not in this mapping, then it is considered
    unhandled by the API.
    """

    INSTRUCTION_STATUS_REASON_MAPPING: ClassVar[
        dict[str, OneTimePaymentInstructionStatusReason],
    ] = {
        "DS02": OneTimePaymentInstructionStatusReason.CANCELLED_BY_PSU,
        "FRAD": OneTimePaymentInstructionStatusReason.REGULATORY_REASON,
        "RR04": OneTimePaymentInstructionStatusReason.REGULATORY_REASON,
    }
    """Instruction status mapping at refresh time."""

    new_token: bool = False
    """Whether we have refreshed/created the token during the session.

    To reduce operation time and number of requests, we only refresh the
    token if it is expired. We do note though that some APIs will randomly
    expire a token sooner than expected. It may also happen that the expiration
    check is made mere seconds before the actual expiration date. If we get
    403 errors when a token is not new, we may want to try to refresh or
    regenerate it.
    """

    # ---
    # Request / response mappings.
    # ---

    oauth_token_page = BrowserParamURL(OAuthTokenPage)

    new_payment_page = BrowserParamURL(
        r"payment-requests$",
        NewPaymentPage,
        methods=("POST",),
    )
    payment_page = BrowserParamURL(
        r"payment-requests/(?P<payment_id>[^/]+)$",
        PaymentPage,
        methods=("GET",),
    )
    payment_cancellation_page = BrowserParamURL(
        r"payment-requests/(?P<payment_id>[^/]+)$",
        PaymentCancellationPage,
        methods=("PUT",),
    )
    payment_confirmation_page = BrowserParamURL(
        r"payment-requests/(?P<payment_id>[^/]+)/confirmation$",
        PaymentPage,
    )
    payment_oauth_confirmation_page = BrowserParamURL(
        r"payment-requests/(?P<payment_id>[^/]+)/o-confirmation$",
        PaymentPage,
    )

    # ---
    # Browser fundamentals.
    # ---

    STATE_CLASS: ClassVar[type[PaymentBrowserState]] = PaymentBrowserState
    state: PaymentBrowserState
    """The state from the browser."""

    @property
    def dialect(self) -> StetPaymentDialect:
        """Get the instantiated payment dialect, using browser configuration.

        :return: The instantiated dialect.
        """
        return self.DIALECT(
            charge_bearer=self.CHARGE_BEARER,
            end_to_end_identifiers_supported=(self.END_TO_END_IDENTIFIERS_SUPPORTED),
            timezone=self.timezone,
            logger=self.logger,
        )

    def setup(self, state: dict | None) -> None:
        """Set up the browser.

        :param state: The raw state to load into the browser.
        """
        if state is None:
            self.state = self.STATE_CLASS()
        else:
            self.state = TypeAdapter(self.STATE_CLASS).validate_python(
                state,
            )

        if self.BASEURL is None:
            raise RuntimeError(
                f"Missing BASEURL definition in {self.__class__.__name__}.",
            )

        self.session.headers["Proxynet-Signature"] = "QSEALC"
        self.session.headers["Proxynet-Signature-Type"] = self.PROXYNET_SIGNATURE_TYPE
        self.proxy_headers["Proxynet-Client-Certificate"] = "QWAC"
        self.setup_session()

        if self.session.headers.get("Proxynet-Signature-Type") != self.PROXYNET_SIGNATURE_TYPE:
            # We actually want to force setting the signature type through
            # the :py:attr:`PROXYNET_SIGNATURE_TYPE` class attribute in
            # order to be able to make stats on it.
            raise AssertionError(
                "Proxynet-Signature-Type must be set through "
                + "the PROXYNET_SIGNATURE_TYPE header.",
            )

        self.BASEURL = self.BASEURL.format(self=self)
        if self.OAUTH_TOKEN_URL is not None:
            self.oauth_token_page = self.oauth_token_page.with_urls(
                self.OAUTH_TOKEN_URL.format(self=self),
            )

        self.logger.info("STET %s browser setup complete.", self.VERSION)

    def dump_state(self) -> dict | None:
        """Dump the browser's state.

        :return: The dumped state.
        """
        return json.loads(self.state.model_dump_json())

    def setup_session(self) -> None:
        """Set up the initialized session.

        This method may take care of setting up client authentication or
        signature algorithms using Proxynet.
        """

    def format_date_header(self) -> str:
        """Build the current date and time in HTTP-Date format.

        HTTP-Date format is defined in RFC 9110, section 5.6.7.
        Some APIs may require another date format, such as ISO 8601.

        :return: The date header contents.
        """
        current_time = mktime(datetime.now().timetuple())
        return format_date_time(current_time)

    def format_accept_language_header(
        self,
        *,
        psu_accept_language: str | None,
    ) -> str | None:
        """Build the 'Accept-Language' for requests.

        The value produced by this method may be used by the API to localize
        the payment validation messages or interfaces.

        :param psu_accept_language: The 'Accept-Language' header from the
            PSU's request, if available.
        :return: The header value if to be defined, or None if not.
        """
        return "fr,fr-FR;q=0.3"

    def prepare_request(self, request: Request) -> PreparedRequest:
        """Prepare the request before it being sent.

        :param request: The request to prepare.
        :return: The prepared request.
        """
        # Load PSU request information if necessary.
        request_information = self.config["request_information"].get()
        if request_information and isinstance(request_information, str):
            request_information = json.loads(request_information)

        # The request timestamp of the call.
        # Some APIs actually require this header to be set.
        request.headers["Date"] = self.format_date_header()

        # ID of the request, unique to the call, as determined by the
        # initiating party (us).
        request.headers["X-Request-ID"] = str(uuid4())

        # Some APIs may require the 'Accept' header to be set to receive JSON.
        if "Accept" not in request.headers:
            if self.ACCEPT_HAL_JSON:
                request.headers["Accept"] = "application/hal+json"
            else:
                request.headers["Accept"] = "application/json"

        # Some APIs require the 'Accept-Language' header to be defined.
        # We use an internal function to build one, eventually based on
        # the PSU's accepted languages if possible.
        psu_accept_language: str | None = None
        if request_information:
            psu_accept_language = request_information.get("Accept-Language")

        accept_language = self.format_accept_language_header(
            psu_accept_language=psu_accept_language or None,
        )
        if accept_language is not None:
            request.headers["Accept-Language"] = accept_language

        # If a JSON payload is present in the request, we want to encode
        # it ourselves in compact mode.
        if request.json is not None:
            data = request.json
            request.json = None
            request.headers["Content-Type"] = "application/json"
            request.data = json.dumps(
                data,
                separators=(",", ":"),
                cls=WoobEncoder,
            ).encode("utf-8")

        # If no 'Authorization' was provided and an OAuth2 token is currently
        # considered active and usable, we want to add the authorization.
        if "Authorization" not in request.headers and self.state.oauth_token is not None:
            request.headers["Authorization"] = (
                f"{self.state.oauth_token_type or 'Bearer'} " + f"{self.state.oauth_token}"
            )

        # If PSU request information is present, we want to send such
        # formatted information to the API.
        if self.INCLUDE_PSU_HEADERS and request_information:
            for key, value in self.build_psu_headers(request_information):
                request.headers[key] = value

        request.headers.update(self.build_optional_headers(request))

        # DigestMixin is in charge of adding the Digest header, as described
        # in section 12.1 "Digest" Header mandatory.
        return super().prepare_request(request)

    def build_optional_headers(self, request: Request) -> dict[str, str]:
        """Build optional headers.

        This method should be overridden in subclasses to add specific
        optional headers to the requests.
        The headers are added to all requests.

        :param request: The request to which the headers are added.
        :return: An iterator for optional headers as tuples of (header name, header value).
        """
        return {}

    def build_psu_headers(
        self,
        request_information: dict[str, str],
    ) -> Iterator[tuple[str, str]]:
        """Build fraud detection oriented headers.

        This is compliant with the STET Documentation Framework, section
        3.6 Fraud detection oriented information.

        :param request_information: The raw request information to get
            PSU context identification from, as transmitted by the caller.
        :return: An iterator for PSU headers as described in section 3.6.
        """
        ip_address = request_information.get("IP-Address")
        if ip_address is not None:
            if not IPv4_RE.fullmatch(ip_address):
                raise ValueError("Expected an IPv4 address.")

            yield "PSU-IP-Address", ip_address

        ip_port = request_information.get("IP-Port")
        if ip_port is not None and ip_port != "":
            int_ip_port = int(ip_port)
            if 0 <= int_ip_port < 65536:
                yield "PSU-IP-Port", ip_port

        http_method = request_information.get("HTTP-Method")
        if http_method:
            yield "PSU-HTTP-Method", http_method

        raw_date = request_information.get("Date")
        if raw_date is not None:
            # Ensure that the date is ISO 8601 formatted.
            yield "PSU-Date", parse_date(raw_date).isoformat()

        for http_header in (
            "User-Agent",
            "Referer",
            "Accept",
            "Accept-Charset",
            "Accept-Encoding",
            "Accept-Language",
        ):
            http_header_value = request_information.get(http_header)
            if http_header_value:
                # Some APIs have hard limits on HTTP header contents.
                # We want to keep these values under a reasonable limit.
                if len(http_header_value) > 50:
                    http_header_value = http_header_value[:47] + "..."

                yield f"PSU-{http_header}", http_header_value

    def raise_for_status_specific(self, response: Response) -> None:
        """Raise exceptions in specific cases.

        This method is meant to be overriden in children browsers, to handle
        specific error cases of a given ASPSP, that is not relevant for the meta
        module STET.
        """
        pass

    def raise_for_status(self, response: Response) -> None:
        """Raise exceptions in some cases.

        :raises StetException: An exception was detected.
        :raises BrowserUnavailable: The target API is down.
        """
        request_id = response.request.headers.get("X-Request-ID")
        if request_id is not None:
            # NOTE: Placing this here means that only requests not ending up in
            # a connection error or timeout will end up in the 'last_*'
            # requests. This may not be exact, since such requests may have had
            # side effects without a response.
            self.payment.extra["last_request_time"] = now_as_utc().isoformat()
            self.payment.extra["last_request_method"] = response.request.method or "GET"
            self.payment.extra["last_request_url"] = response.url
            self.payment.extra["last_request_id"] = request_id

        if 400 <= response.status_code < 600:
            self.logger.info("raise_for_status called with code %s", response.status_code)

            self.payment.extra["error_code"] = str(response.status_code)
            self.payment.extra["error_message"] = response.text

            self.raise_for_status_specific(response)
            # Detect generic STET errors.
            page = ErrorPage(self, response)
            msg = None
            try:
                page.raise_if_error_found()
            except Exception as e:
                if response.status_code < 500:
                    raise e
                if isinstance(e, StetException):
                    msg = e.detail

            if response.status_code >= 500:
                raise BrowserUnavailable(msg)

        super().raise_for_status(response)

    @property
    def is_validation_redirect_skippable(self) -> bool:
        return self.VALIDATION_REDIRECT_FLOW in (
            RedirectFlow.SIMPLE,
            RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION,
        )

    # ---
    # utilities
    # ---

    def should_retry_with_new_token(self, response: Response) -> bool:
        """Check if the request should be retried with a new token.

        This method checks a failed request response and determine whether it
        should be retried with a new token.
        This is useful to use when an API token is reused in the browser, while
        it could have been:
        - expired (if the expiration lookahead was not sufficient).
        - revoked or invalidated by the API.
        - any reason causing a forbidden access to the API endpoint.

        The meta module implementation of this method will only check the
        response status code. But a child module could find more useful to
        check the actual response content or headers, since a 403 might be
        caused by other reasons than the token.
        Note: This method will return False when the module uses no pre step
        authentication.

        :param response: The request response.
        :return: True if the request should be retried with a new token, False
            otherwise.
        """
        return (
            response.status_code == 403
            and not self.new_token
            and self.PRE_STEP_TYPE != PreStepType.NONE
        )

    # ---
    # Common OAuth2 utilities.
    # ---

    def get_oauth_client_credentials(self) -> tuple[str, str | None]:
        """Get the client credentials for OAuth2 operations, if relevant.

        This method can be overridden if different Woob values are used
        for storing the PIS client credentials.

        :return: The (client_id, client_secret) pair.
        """
        client_secret_value = self.config.get("client_secret")
        client_secret: str | None = None
        if client_secret_value is not None:
            client_secret = client_secret_value.get() or None

        return (
            self.config["client_id"].get(),
            client_secret,
        )

    @contextmanager
    def different_token_endpoint(
        self,
        url: str,
        *,
        headers: dict | None = None,
    ) -> Iterator[None]:
        """Set a different token URL in a context.

        This is mostly useful if, for example, the token request in the
        validation or cancellation flow uses a different token URL.

        An example usage of this method is the following:

        .. code-block:: python

            class MyStetPaymentBrowser(Stet151PaymentBrowser):
                BASEURL = 'https://api.example/{self.BANK}/stet/'
                OAUTH_TOKEN_URL = 'https://api.example/{self.BANK}/oauth/token'
                ALT_TOKEN_URL = 'https://api.example/{self.BANK}/v-token'

                BANK = 'my-bank'

                def request_validation_token(self, **kwargs) -> None:
                    with self.different_token_endpoint(self.ALT_TOKEN_URL):
                        super().request_validation_token(**kwargs)

        :param url: The URL pattern.
        :param headers: The new headers to be set, if necessary.
        """
        original_oauth_token_page = self.oauth_token_page
        new_oauth_token_page = original_oauth_token_page.with_urls(
            url.format(self=self),
            clear=True,
        )
        if headers is not None:
            new_oauth_token_page = new_oauth_token_page.with_headers(headers)

        self.oauth_token_page = new_oauth_token_page
        try:
            yield
        finally:
            self.oauth_token_page = original_oauth_token_page

    def request_oauth_token(
        self,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> None:
        """Make the pre-step token request and update the browser state.

        After a call to request_oauth_token, the browser `new_token` attribute
        will always be True. Use this attribute to check if the token has
        been updated during this session.

        :param data: The form data to use.
        :param headers: The additional headers to include in the request.
        """
        self.logger.info("Requesting OAuth token")

        headers = headers or {}
        if "Accept" not in headers:
            headers["Accept"] = "application/json"

        page = self.oauth_token_page.go(
            data=data,
            headers=headers,
        )

        token_data = page.get_token_data()
        expires_at = token_data.expires_at
        if expires_at and expires_at.tzinfo:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)

        token_type = token_data.token_type or "Bearer"
        # Some APIs may not actually accept the token type they return,
        # for example Crédit Agricole returns 'bearer' but actually requires
        # 'Bearer' (capitalized) to be used.
        # In such cases, we want to allow a mapping here.
        if token_type in self.OAUTH_TOKEN_TYPE_ALIASES:
            token_type = self.OAUTH_TOKEN_TYPE_ALIASES[token_type]

        self.state.oauth_token = token_data.token
        self.state.oauth_token_type = token_type
        self.state.oauth_token_expires_at = expires_at
        self.state.oauth_refresh_token = token_data.refresh_token
        self.new_token = True

    def refresh_oauth_token_if_possible(self) -> bool:
        """Use the refresh token to obtain a new, up to date token.

        If this method succeeds, the :py:attr:`state.oauth_token`` is set to
        the token to use for later requests. Otherwise,
        :py:attr:`state.oauth_token` will be set to :py:data:`None`.

        Note that the refresh operation will use the same pair of client
        credentials as used when initially requesting the token.

        :return: True if the token was refreshed, False otherwise.
        """
        self.state.oauth_token = None
        if self.state.oauth_refresh_token is None:
            return False

        client_id, client_secret = self.get_oauth_client_credentials()

        headers = {}
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.state.oauth_refresh_token,
            "redirect_uri": self.config["redirect_uri"].get(),
        }

        if self.OAUTH_CLIENT_AUTH_METHOD == OAuthClientAuthMethod.BASIC:
            basic_auth = b64encode(
                f"{client_id}:{client_secret or ''}".encode("ascii"),
            ).decode("ascii")
            headers["Authorization"] = f"Basic {basic_auth}"
        else:
            data["client_id"] = client_id
            if client_secret is not None:
                data["client_secret"] = client_secret

        self.request_oauth_token(data=data, headers=headers)
        return True

    def check_oauth_token(self) -> None:
        """Check the OAuth2 token, if any."""
        if self.state.oauth_token is None:
            # No OAuth2 token to take care of, we're fine here!
            return

        if self.state.oauth_token_expires_at is None or not self.is_token_expired(
            self.state.oauth_token_expires_at
        ):
            # The token hasn't expired yet.
            return

        # We have an OAuth2 token, which might have been obtained either
        # through a pre-step or through a payment validation flow.
        # We want to try to refresh it if we can.
        self.new_token = self.refresh_oauth_token_if_possible()

    def reset_oauth_token(self) -> None:
        """Reset the current OAuth2 token."""
        self.logger.info("Resetting OAuth2 token data.")
        self.state.oauth_token = None
        self.state.oauth_token_type = None
        self.state.oauth_refresh_token = None
        self.state.oauth_token_expires_at = None

    # ---
    # Common validation utilities.
    # ---

    def reset_validation(self) -> None:
        """Reset validation-specific properties.

        This is useful for resetting both initiation and cancellation
        validation.
        """
        self.logger.info("Resetting validation properties.")
        self.state.payment_nonce = None
        self.state.validation_approach = ValidationApproach.NONE
        self.state.pkce_verifier = None
        self.state.oauth_authorisation_code = None
        self.state.oauth_token_to_be_requested = False
        self.state.psu_auth_factor = None
        self.state.validation_confirmation_required = False
        self.state.validation_confirmed = False

    # ---
    # Authn / authz pre-step handling.
    # ---

    def request_pre_step_client_token(self) -> None:
        """Request a pre-step token using the client credentials grant."""
        self.logger.info("Requesting pre-step client token")

        client_id, client_secret = self.get_oauth_client_credentials()

        headers = {}
        data = {
            "grant_type": "client_credentials",
            "scope": self.PRE_STEP_OAUTH_SCOPE,
        }

        if self.OAUTH_CLIENT_AUTH_METHOD == OAuthClientAuthMethod.BASIC:
            basic_auth = b64encode(
                f"{client_id}:{client_secret or ''}".encode("ascii"),
            ).decode("ascii")
            headers["Authorization"] = f"Basic {basic_auth}"
        else:
            data["client_id"] = client_id
            if client_secret is not None:
                data["client_secret"] = client_secret

        self.request_oauth_token(data=data, headers=headers)

    def check_pre_step(self) -> None:
        """Check that pre-step auth is valid.

        If an OAuth2 token exists, it might not have been obtained through
        a pre-step, so we refresh it if necessary. Otherwise, we only obtain
        one if the pre-step is an OAuth2 client credentials grant.
        """
        self.logger.info("Checking pre-step authentication")
        self.check_oauth_token()
        if self.state.oauth_token is not None:
            # We already have a valid token, no need to do another
            # authn / authz pre-step!
            return

        if self.PRE_STEP_TYPE == PreStepType.NONE:
            return

        self.request_pre_step_client_token()

    # ---
    # Validation-specific methods.
    # ---

    def build_validation_redirect_url(
        self,
        *,
        pkce_data: PKCEData | None,
    ) -> str | None:
        """Build the redirect URL for the validation payload.

        :param pkce_data: The data to build the redirect URL from, if
            relevant.
        :return: The redirect URL, or None if no successful report URL is
            to be placed in the validation payload.
        """
        if not self.VALIDATION_REDIRECT_URLS_REQUIRED and self.VALIDATION_REDIRECT_FLOW in (
            RedirectFlow.OAUTH_AUTHORISATION_CODE,
            RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
        ):
            return None

        kwargs = {}
        if pkce_data is not None:
            kwargs.update(
                {
                    "code_challenge": pkce_data.challenge,
                    "code_challenge_method": pkce_data.method,
                }
            )

        return get_url_with_params(
            self.config["redirect_uri"].get(),
            state=self.get_redirect_state(),
            **kwargs,
        )

    def build_validation_error_url(
        self,
        *,
        pkce_data: PKCEData | None,
    ) -> str | None:
        """Build the error redirect URL for the validation payload.

        :param pkce_data: The data to build the redirect URL from, if
            relevant.
        :return: The error redirect URL, or None if no unsuccessful report URL
            is to be placed in the validation payload.
        """
        if not self.VALIDATION_REDIRECT_URLS_REQUIRED and self.VALIDATION_REDIRECT_FLOW in (
            RedirectFlow.OAUTH_AUTHORISATION_CODE,
            RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
        ):
            return None

        kwargs = {}
        if pkce_data is not None:
            kwargs.update(
                {
                    "code_challenge": pkce_data.challenge,
                    "code_challenge_method": pkce_data.method,
                }
            )

        return get_url_with_params(
            self.config["error_uri"].get(),
            state=self.get_redirect_state(),
            **kwargs,
        )

    def build_validation_pkce_data(
        self,
        *,
        challenge_type: PKCEChallengeType,
    ) -> PKCEData:
        """Build PKCE data suitable for the validation flow.

        :param challenge_type: The challenge type to use.
        :return: The built PKCE data.
        """
        return PKCEData.build(challenge_type)

    def build_validation_supplementary_data(
        self,
        *,
        pkce_data: PKCEData | None,
    ) -> dict[str, Any]:
        """Build supplementary data for when creating a payment.

        :param pkce_data: The optional PKCE data to include in report URLs.
        :return: The supplementary data to include as 'supplementaryData'.
        """
        successful_report_url = self.build_validation_redirect_url(
            pkce_data=pkce_data,
        )
        unsuccessful_report_url = self.build_validation_redirect_url(
            pkce_data=pkce_data,
        )

        data: dict[str, Any] = {"acceptedAuthenticationApproach": ["REDIRECT"]}
        if successful_report_url is not None:
            data["successfulReportUrl"] = successful_report_url
        if unsuccessful_report_url is not None:
            data["unsuccessfulReportUrl"] = unsuccessful_report_url

        return data

    def get_payment_status_data(self) -> PaymentStatusData:
        """Get status data for the payment.

        :return: The raw payment status data.
        """
        self.logger.info("Getting payment status data")
        if (
            self.payment_confirmation_page.is_here(payment_id=self.payment.id)
            or self.payment_oauth_confirmation_page.is_here(
                payment_id=self.payment.id,
            )
            or self.payment_page.is_here(payment_id=self.payment.id)
        ):
            # If we're in a check after confirmation, no need to request the
            # payment page again, we can just use information on the current
            # page!
            page = self.page
        else:
            # The token might have been reset after a confirmation, meaning
            # that it may need to be re-requested.
            self.check_pre_step()
            self.logger.info("Requesting payment page")
            page = self.payment_page.go(payment_id=self.payment.id)

        data = page.get_status_data()
        if data.status:
            self.payment.extra["last_status"] = data.status
        else:
            del self.payment.extra["last_status"]
        if data.status_reason:
            self.payment.extra["last_status_reason"] = data.status_reason
        else:
            del self.payment.extra["last_status_reason"]

        return data

    def check_current_validation_status(self) -> None:
        """Check the status for the current payment.

        If this method returns, it means that the authorisation is still
        ongoing.

        :raises PaymentValidationError: The status corresponds to a validation
            error regarding the payment.
        :raises PaymentConfirmationRequired: A confirmation is currently
            expected for the payment.
        :raises PaymentInteractionSkipped: The current authorisation is
            finished.
        """
        self.logger.info("Checking current validation status")
        data = self.get_payment_status_data()

        if data.status in ("RJCT", "CANC"):
            self.logger.info(
                "Payment status is %s, getting validation error code",
                data.status,
            )
            if data.status_reason is None:
                raise PaymentValidationError()

            try:
                reason_code, message = self.VALIDATION_ERROR_CODE_MAPPING[data.status_reason]
                self.logger.info(
                    "Payment rejected with code %s -> %s : %s",
                    data.status_reason,
                    reason_code,
                    message,
                )
            except KeyError:
                message = "Unknown error"
                self.logger.error(
                    "No rejection code known for raw status reason: %s",
                    data.status_reason,
                )
                reason_code = PaymentValidationErrorCode.OTHER

            raise PaymentValidationError(message, code=reason_code)

        if data.status in ("RCVD", "ACTC"):
            self.logger.info(
                "Payment status is %s, need action from PSU",
                data.status,
            )
            # RCVD means the payment was simply created on the ASPSP's side.
            # ACTC means it was accepted technically, and needs acceptation.
            # ACCO means it is awaiting confirmation, which is still
            # technically occuring within payment validation.
            return

        if data.status == "ACCO":
            self.logger.info(
                "Payment status is %s, waiting for confirmation",
                data.status,
            )
            # The API requests a confirmation explicitely.
            raise PaymentConfirmationRequired()

        if data.status in ("ACCP", "ACSP"):
            if self.VALIDATION_MAY_HAVE_AUTOMATIC_CONFIRMATION:
                # Whether we have requested a confirmation or not, the
                # payment validation is finished!
                self.logger.info(
                    (
                        "Payment status is %s and has been automatically "
                        "confirmed by the ASPSP, raising interaction skipped."
                    ),
                    data.status,
                )
                raise PaymentInteractionSkipped()

            # Some APIs may return an 'ACCP', or even an 'ACSP' status, while a
            # confirmation is still required, so we need to consider ourselves
            # in a validating state even though the status shouldn't reflect
            # that.
            if self.state.validation_confirmed:
                # Validation has been confirmed, this check should not have
                # happened.
                self.logger.info(
                    (
                        "Payment status is %s but it has already been "
                        "confirmed, raising interaction skipped."
                    ),
                    data.status,
                )
                raise PaymentInteractionSkipped()

            if self.state.validation_approach == ValidationApproach.REDIRECT:
                self.logger.info("Redirect approach is REDIRECT")

                if self.VALIDATION_REDIRECT_FLOW == RedirectFlow.SIMPLE:
                    # Confirmation is required to continue.
                    # If the confirmation has already been required, it is
                    # to the caller to determine this.
                    self.logger.info("Redirect flow is SIMPLE, raising confirmation required.")
                    raise PaymentConfirmationRequired()

                if self.VALIDATION_REDIRECT_FLOW == (RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION):
                    # No more steps are required, we can just finish the
                    # payment creation and validation process!
                    self.logger.info(
                        "Redirect flow is SIMPLE WITHOUT CONFIRMATION, raisinginteraction skippeed."
                    )
                    raise PaymentInteractionSkipped()

            # User input (callback, ...) is still required for payment
            # validation to continue.
            self.logger.info(
                (
                    "Payment status is %s but a user input is still required "
                    "for the validation to continue."
                ),
                data.status,
            )
            return

        self.logger.info(
            "Payment status is %s, raising interaction skipped.",
            data.status,
        )
        raise PaymentInteractionSkipped()

    def request_validation_token(self, *, code: str) -> None:
        """Request a token for validating a payment.

        This uses the OAuth2 Authorization Code grant.

        :param code: The authorization code to obtain a token with.
        """
        self.logger.info("Requesting validation token with user code.")
        client_id, client_secret = self.get_oauth_client_credentials()

        headers = {}
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config["redirect_uri"].get(),
        }

        if self.state.pkce_verifier:
            data["code_verifier"] = self.state.pkce_verifier

        if self.OAUTH_CLIENT_AUTH_METHOD == OAuthClientAuthMethod.BASIC:
            basic_auth = b64encode(
                f"{client_id}:{client_secret or ''}".encode("ascii"),
            ).decode("ascii")
            headers["Authorization"] = f"Basic {basic_auth}"
        else:
            data["client_id"] = client_id
            if client_secret is not None:
                data["client_secret"] = client_secret

        self.request_oauth_token(data=data, headers=headers)

    def confirm_payment_validation(
        self,
        *,
        psu_auth_factor: str | None,
    ) -> None:
        """Confirm payment validation.

        This uses the explicit payment confirmation endpoints provided by the
        standard.

        :param psu_auth_factor: The authentication factor from the PSU,
            if relevant.
        """
        self.logger.info("confirming the payment validation.")

        payload = {}
        if self.state.payment_nonce is not None:
            payload["nonce"] = self.state.payment_nonce
        if psu_auth_factor is not None:
            payload["psuAuthenticationFactor"] = psu_auth_factor

        self.payment_confirmation_page.go(
            payment_id=self.payment.id,
            json=payload,
        )

    def confirm_payment_oauth_validation(self) -> None:
        """Confirm the payment following an OAuth2 flow."""
        self.logger.info("confirming the payment with an OAuth2 flow.")

        payload = {}
        if self.state.payment_nonce is not None:
            payload["nonce"] = self.state.payment_nonce

        if (
            self.VALIDATION_OAUTH_CONFIRMATION_FACTOR == (OAuthRedirectFlowConfirmationFactor.CODE)
            and self.state.oauth_authorisation_code is not None
        ):
            self.logger.info("Payment confirmation factor is a code.")
            payload["psuAuthenticationFactor"] = self.state.oauth_authorisation_code
        elif (
            self.VALIDATION_OAUTH_CONFIRMATION_FACTOR == (OAuthRedirectFlowConfirmationFactor.TOKEN)
            and self.state.oauth_token is not None
        ):
            self.logger.info("Payment confirmation factor is a token.")
            payload["psuAuthenticationFactor"] = self.state.oauth_token

        if self.VALIDATION_COMMON_OAUTH_CONFIRMATION:
            # STET 1.5.0+ style, where the confirmation endpoint is the same
            # for simple and OAuth2 Authorisation Code redirect flows.
            # This style mostly exists because OAuth2 Authorisation Code
            # flows for payment validation becomes the default style.
            self.payment_confirmation_page.go(
                payment_id=self.payment.id,
                json=payload,
            )
        else:
            # Pre-STET 1.5.0 style, where a dedicated confirmation endpoint
            # is used for OAuth2 Authorisation Code redirect flows.
            self.payment_oauth_confirmation_page.go(
                payment_id=self.payment.id,
                json=payload,
            )

    def detect_validation_callback_error(self, callback_url: str) -> None:
        """Detect a validation callback error.

        This should only raise validation errors if an error is detected;
        otherwise, it can safely do nothing.

        Note that this method is only called if
        :py:attr:`UNSAFE_URL_ERROR_DETECTION` is set to ``True``.

        :param callback_url: The URL to detect the validation error from.
        :raises PaymentValidationError: A validation error has been detected
            from the URL.
        """
        self.logger.info("Trying to detect a validation callback error.")
        self.logger.warning(
            "This should only be done if payment confirmation from the TPP is needed by the ASPSP."
        )

        params = get_url_params(callback_url)
        error_code = params.get("error") or params.get("error_code")
        if error_code is None:
            self.logger.info(
                "No error or error_code found in the callback URL.",
            )
            return

        error_detail = params.get("error_description")
        raise PaymentValidationError(error_detail or "")

    def initialize_payment_validation(
        self,
        page: NewPaymentPage,
        *,
        pkce_data: PKCEData | None,
    ) -> None:
        """Initialize the payment validation.

        :param page: The page from which the approach can be determined.
        :raises BrowserInteraction: An interaction is required from the PSU.
        """
        self.logger.info("Initializing payment validation.")

        data = page.get_validation_data()
        self.state.payment_nonce = data.nonce
        self.state.validation_approach = data.approach

        if data.approach == ValidationApproach.REDIRECT:
            self.logger.info("Payment validation approach is REDIRECT.")

            link = data.links["consentApproval"]

            if self.VALIDATION_REDIRECT_FLOW in (
                RedirectFlow.SIMPLE,
                RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION,
                RedirectFlow.SIMPLE_WITH_AUTH_FACTOR,
            ):
                self.logger.info(
                    "The payment redirect flow is %s, raising PaymentRedirect.",
                    self.VALIDATION_REDIRECT_FLOW,
                )
                raise PaymentRedirect(
                    link,
                    can_skip=self.is_validation_redirect_skippable,
                )

            if self.VALIDATION_REDIRECT_FLOW not in (
                RedirectFlow.OAUTH_AUTHORISATION_CODE,
                RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
            ):
                self.logger.error(
                    "Unknown redirect flow in module: %s",
                    self.VALIDATION_REDIRECT_FLOW,
                )
                raise NotImplementedError(
                    "Unhandled redirect flow at validation initialization: "
                    + f"{self.VALIDATION_REDIRECT_FLOW!r}",
                )

            client_id, _ = self.get_oauth_client_credentials()

            code_challenge: str | None = None
            code_challenge_method: str | None = None
            if pkce_data is not None:
                code_challenge = pkce_data.challenge
                code_challenge_method = pkce_data.method

            # The following values should be prevalued by the ASPSP:
            # * 'response_type': prevalued with 'code'.
            # * 'scope': prevalued with 'pisp'.
            # * 'context': prevalued with a hint to the payment-request.
            #
            # We need to complete this URL with our own parameters:
            # * 'state'.
            # * 'redirect_uri', to be used in place of the
            #   'successfulReportUrl' as set in the payment request.
            #
            # We also add PKCE related parameters in case the extension is
            # to be used with the ASPSP.
            self.logger.info(
                "The payment redirect flow is %s, raising PaymentRedirect.",
                self.VALIDATION_REDIRECT_FLOW,
            )
            raise PaymentRedirect(
                get_url_with_params(
                    link,
                    state=self.get_redirect_state(),
                    client_id=client_id,
                    redirect_uri=self.config["redirect_uri"].get(),
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                )
            )

        self.logger.error(
            "Unknown validation approach in module: %s",
            data.approach,
        )
        raise NotImplementedError(f"Unhandled approach {data.approach!r}")

    def resume_payment_validation_redirect(
        self,
        *,
        confirmation_required: bool,
    ) -> None:
        """Resume redirection in the context of a payment validation.

        :param confirmation_required: Whether an explicit confirmation has
            been required by the payment status check or not.
        :raises BrowserInteraction: An interaction is expected.
        """
        self.logger.info("Resuming payment validation redirect.")

        should_confirm = self.config["confirm"].get()
        if should_confirm:
            if should_confirm == "false":
                # The caller wants to cause a payment validation error.
                # We actually just need to forget the codes we had, and
                # raise an error for the storage to be unusable after a
                # short period of time.
                self.logger.info("The payment confirmation was explicitly refused by the caller.")
                raise PaymentValidationError(
                    code=PaymentValidationErrorCode.CONFIRMATION_REFUSED,
                )

            self.logger.info("Caller is requesting the payment confirmation.")

            if self.VALIDATION_REDIRECT_FLOW in (
                RedirectFlow.OAUTH_AUTHORISATION_CODE,
                RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
            ):
                if (
                    self.state.oauth_token_to_be_requested
                    and self.state.oauth_authorisation_code is not None
                ):
                    self.state.oauth_token_to_be_requested = False

                    # We have actually interrupted ourselves before requesting
                    # a token using the provided OAuth2 authorisation code.
                    # We may do this now.
                    #
                    # Note that if this token request fails because the
                    # provided code was actually invalid, we cannot go back to
                    # the PSU validation anymore; the payment will just fail,
                    # and cause the whole creation and validation to fail.
                    self.request_validation_token(
                        code=self.state.oauth_authorisation_code,
                    )

                if self.VALIDATION_REDIRECT_FLOW == (RedirectFlow.OAUTH_AUTHORISATION_CODE):
                    # An additional confirmation request is necessary.
                    self.confirm_payment_oauth_validation()

                # We want to reset the token to force requesting a new token on
                # next call, since with some ASPSPs, the token obtained using
                # the OAuth2 Authorisation Code grant cannot be used for other
                # operations such as cancellation.
                self.reset_oauth_token()
            else:
                code = self.state.psu_auth_factor
                self.confirm_payment_validation(psu_auth_factor=code)

            # The payment validation should have been confirmed.
            self.logger.info("The payment validation has been confirmed.")
            self.state.validation_confirmed = True
            return

        callback_url = self.config["auth_uri"].get()
        if callback_url is None or not callback_url:
            self.logger.info(
                "No callback URL provided in the woob call, raising PaymentSameInteraction."
            )
            raise PaymentSameInteraction()

        if self.VALIDATION_REDIRECT_FLOW in (
            RedirectFlow.OAUTH_AUTHORISATION_CODE,
            RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
        ):
            if callback_url == PaymentRedirect.SKIPPED_VALUE:
                # Oh no, the callback was reported as skippable but it
                # actually isn't! Just fall back on our tracks.
                self.logger.warning(
                    "The callback has been declared skippable with approach "
                    + '"%s" that does not support it.',
                    self.VALIDATION_REDIRECT_FLOW,
                )
                raise PaymentSameInteraction()

            auth_code = get_url_param(callback_url, "code", default=None)
            if auth_code is None:
                # The payment request on the bank's side is not reported
                # as rejected, so even if there's an error present in the
                # callback URL, we do not want to report the payment validation
                # as failed.
                self.logger.info(
                    (
                        "The payment redirect flow is %s but no authorization "
                        "code was given, we cannot proceed."
                    ),
                    self.VALIDATION_REDIRECT_FLOW,
                )
                raise PaymentSameInteraction()

            self.state.oauth_authorisation_code = auth_code
            if (
                self.VALIDATION_REDIRECT_FLOW
                == RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION
                and self.UNSAFE_CONFIRMATIONS
            ):
                self.logger.info(
                    "Unsafe confirmation enabled, the payment confirmation "
                    "is temporarily put on hold while waiting for the caller."
                )
                self.state.oauth_token_to_be_requested = True
                self.state.validation_confirmation_required = True
                raise PaymentConfirmationRequired()

            # We want to request the validation token now.
            self.request_validation_token(code=auth_code)

            if self.VALIDATION_REDIRECT_FLOW == (RedirectFlow.OAUTH_AUTHORISATION_CODE):
                self.logger.info(
                    "Got the payment confirmation code from the Oauth flow, "
                    "waiting for the caller confirmation."
                )
                self.state.validation_confirmation_required = True
                raise PaymentConfirmationRequired()

            # The payment is now confirmed. We want to reset the token to force
            # requesting a new token on next call, since with some ASPSPs,
            # the token obtained using the OAuth2 Authorisation Code grant
            # cannot be used for other operations such as cancellation.
            self.reset_oauth_token()
        elif self.VALIDATION_REDIRECT_FLOW == RedirectFlow.SIMPLE_WITH_AUTH_FACTOR:
            if callback_url == PaymentRedirect.SKIPPED_VALUE:
                # Oh no, the callback was reported as skippable but it
                # actually isn't! Just fall back on our tracks.
                self.logger.warning(
                    "The callback has been declared skippable with approach "
                    + '"%s" that does not support it.',
                    self.VALIDATION_REDIRECT_FLOW,
                )
                raise PaymentSameInteraction()

            psu_auth_factor = get_url_param(
                callback_url,
                "psuAuthenticationFactor",
                default=None,
            )
            if psu_auth_factor is None:
                # The payment request on the bank's side is not reported
                # as rejected, so even if there's an error present in the
                # callback URL, we do not want to report the payment
                # validation as failed.
                self.logger.info(
                    "No psu auth factor in the callback URL, but the payment "
                    "is not yet rejected by the bank"
                )
                raise PaymentSameInteraction()

            if self.UNSAFE_CONFIRMATIONS:
                self.state.psu_auth_factor = psu_auth_factor
                self.state.validation_confirmation_required = True
                self.logger.info(
                    "Unsafe confirmation enabled, the payment confirmation is"
                    " temporarily put on hold while waiting for the caller."
                )
                raise PaymentConfirmationRequired()

            self.confirm_payment_validation(psu_auth_factor=psu_auth_factor)
        elif self.VALIDATION_REDIRECT_FLOW == RedirectFlow.SIMPLE:
            # We don't have a lot of way to know whether we can confirm the
            # payment or not:
            # * If we have the 'ACCP' or 'ACSP' status but haven't confirmed
            #   yet.
            # * If we have the 'ACCO' status (which only appears in STET 1.6.2
            #   officially), it means that we are able to confirm directly.
            # * Otherwise, if we have a callback, we may want to try to
            #   confirm, and see what happens.
            if confirmation_required:
                # Two situations can lead to this case:
                # * We have the 'ACCO' status (which only appears in STET
                #   1.6.2), which explicitely means that a confirmation is
                #   required.
                # * We have the 'ACCP' or 'ACSP' status and are configured
                #   with a redirect flow including a confirmation.
                self.state.psu_auth_factor = None
                self.state.validation_confirmation_required = True
                self.logger.info("Payment confirmation required with flow SIMPLE")
                raise PaymentConfirmationRequired()

            if self.VALIDATION_CONFIRMATION_STATUS_REQUIRED:
                # We expect a payment status explicitely telling us that we
                # can confirm the payment here.
                self.logger.info(
                    "We are expecting a specific payment status before confirming the payment."
                )
                raise PaymentSameInteraction()

            if callback_url == PaymentRedirect.SKIPPED_VALUE:
                # If we don't have a callback yet, we want to wait for one
                # before trying to confirm.
                self.logger.info(
                    "create_one_time_payment called with the skipped value "
                    "for callback, but we are expecting to confirm the payment"
                    " here. Raising PaymentSameInteraction."
                )
                raise PaymentSameInteraction()

            if self.UNSAFE_CONFIRMATIONS:
                # This is very unsafe, since we don't actually know for
                # certain if the payment is ready to be confirmed!
                self.state.psu_auth_factor = None
                self.state.validation_confirmation_required = True
                self.logger.info(
                    "Unsafe confirmation enabled, the payment confirmation "
                    "is temporarily put on hold while waiting for the caller."
                )
                raise PaymentConfirmationRequired()

            self.confirm_payment_validation(psu_auth_factor=None)
        else:
            # We're in the case of a simple redirect without confirmation,
            # and we've checked that the payment is still being validated by
            # checking the status before calling this method, so we have
            # to wait at this point.
            self.logger.info("Payment is still waiting for a PSU action.")
            raise PaymentSameInteraction()

        self.logger.info("The payment validation has been confirmed.")
        self.state.validation_confirmed = True

    def resume_payment_validation(self) -> None:
        """Resume the payment validation.

        :raises BrowserInteraction: An interaction is expected.
        """
        self.logger.info("Resuming payment validation.")

        if self.state.first_failure_at is not None:
            # The payment validation was already detected as failed.
            # From here, either we decide still not to report the payment
            # validation as failed to keep waiting for the callback, or
            # we raise a validation error.
            callback_uri: str | None = None
            if "auth_uri" in self.config:
                callback_uri = self.config["auth_uri"].get()

            if self.UNSAFE_URL_ERROR_DETECTION:
                if callback_uri is not None and callback_uri != PaymentRedirect.SKIPPED_VALUE:
                    # We have a callback and are still able to detect errors
                    # from callback URLs, we go for it!
                    # However, note that if we're not able to detect an
                    # error in the callback, we will either keep waiting
                    # or raise a generic validation error.
                    self.detect_validation_callback_error(callback_uri)

                if (
                    self.VALIDATION_FAILURE_CALLBACK_DETECTION_TIMEOUT is not None
                    and self.state.first_failure_at
                    + self.VALIDATION_FAILURE_CALLBACK_DETECTION_TIMEOUT
                    > datetime.utcnow()
                ):
                    # We're still within the allowed time span, we want to
                    # keep waiting for the next redirect.
                    self.logger.info("Waiting for a PSU Errback.")
                    raise PaymentSameInteraction()

            if self.state.first_failure_validation_data is not None:
                # The code and message are defined from the first failure!
                # This should always be the case, but the typing can't
                # reflect this reality.
                code, message = self.state.first_failure_validation_data
                raise PaymentValidationError(message or "", code=code)

            raise PaymentValidationError()

        confirmation_required = False
        try:
            self.check_current_validation_status()
        except PaymentConfirmationRequired:
            # A confirmation is explicitely required by the status check.
            self.logger.info("Payment confirmation is expected.")
            confirmation_required = True
        except PaymentValidationError as exc:
            if self.state.validation_approach != ValidationApproach.REDIRECT:
                raise

            # If the current approach is REDIRECT and unsafe URL error
            # detection is enabled, we may want to either delay the
            # validation error reporting or detect from the URL.
            if not self.UNSAFE_URL_ERROR_DETECTION:
                raise

            self.logger.info("Unsafe url error detection is enabled.")

            callback_uri = self.config["auth_uri"].get()
            if callback_uri is not None and callback_uri != PaymentRedirect.SKIPPED_VALUE:
                self.detect_validation_callback_error(callback_uri)
                raise

            if self.VALIDATION_FAILURE_CALLBACK_DETECTION_TIMEOUT is None:
                raise

            self.logger.info("Waiting for a PSU Errback to extract rejection information.")

            # We're keeping in memory that the payment validation has failed,
            # but we do not report it yet, in order to get a callback with
            # more information later. Note that from this point on, we
            # should not be making any more requests to the bank regarding
            # the payment request, we do everything from memory.
            self.state.first_failure_at = datetime.utcnow()
            self.state.first_failure_validation_data = (
                exc.code,
                str(exc) or None,
            )

            self.logger.warning(
                "Payment has failed with code %r and message %r, but a "
                + "callback is expected to determine the exact error code "
                + "before %s",
                exc.code,
                str(exc),
                (
                    self.state.first_failure_at + self.VALIDATION_FAILURE_CALLBACK_DETECTION_TIMEOUT
                ).isoformat(),
            )
            raise PaymentSameInteraction() from exc
        except PaymentInteractionSkipped:
            # The payment has already been validated, no need for the rest
            # of this method!
            return

        validation_approach = self.state.validation_approach
        if validation_approach == ValidationApproach.REDIRECT:
            self.resume_payment_validation_redirect(
                confirmation_required=confirmation_required,
            )
        else:
            self.logger.error(
                "Unknown validation approach for STET: %s",
                validation_approach,
            )
            raise NotImplementedError(
                f"Unhandled validation approach {validation_approach!r}",
            )

    # ---
    # Refresh specific methods.
    # ---

    def set_instruction_status(
        self,
        instruction: OneTimePaymentInstruction,
        *,
        payment_status: str,
        payment_status_reason: str | None,
        instruction_status: str | None,
        instruction_status_reason: str | None,
    ) -> None:
        """Set the status of an instruction.

        :param instruction: The instruction on which to set status data.
        :param payment_status: The raw status of the payment.
        :param payment_status_reason: The raw status reason of the payment,
            if available.
        :param instruction_status: The raw status of the instruction, if
            available.
        :param instruction_status_reason: The raw status reason of the
            instruction, if available.
        :raises AssertionError: Invalid statuses have been provided.
        """
        self.logger.info("Setting the payment instructions statuses.")

        raw_status = instruction_status or payment_status
        if instruction_status == "ACTC":
            # Some APIs, such as Arkea APIs (Fortuneo), report 'ACTC'
            # instruction statuses with a correct status on the payment,
            # e.g. 'ACSP'. In such cases, we just take the payment status.
            raw_status = payment_status

        if raw_status == "ACSC":
            instruction.status = OneTimePaymentInstructionStatus.DONE
        elif raw_status == "CANC":
            instruction.status = OneTimePaymentInstructionStatus.REJECTED
            instruction.status_reason = OneTimePaymentInstructionStatusReason.CANCELLED_BY_PSU
        elif raw_status == "RJCT":
            raw_status_reason = instruction_status_reason or payment_status_reason
            if not raw_status_reason:
                status_reason = OneTimePaymentInstructionStatusReason.NONE
            else:
                try:
                    status_reason = self.INSTRUCTION_STATUS_REASON_MAPPING[raw_status_reason]
                except KeyError:
                    self.logger.warning(
                        "Unhandled status reason %r",
                        raw_status_reason,
                    )
                    status_reason = OneTimePaymentInstructionStatusReason.OTHER

            instruction.status = OneTimePaymentInstructionStatus.REJECTED
            instruction.status_reason = status_reason
        elif raw_status in ("ACCP", "ACSP", "PDNG"):
            # ACCP only appears at payment-level.
            instruction.status = OneTimePaymentInstructionStatus.PENDING
        elif instruction_status is not None:
            self.logger.error("Unhandled instruction status: %s", instruction_status)
            raise NotImplementedError(
                "Unhandled instruction status at refresh: " + f"{instruction_status!r}",
            )
        else:
            self.logger.error("Unhandled payment status: %s", payment_status)
            raise NotImplementedError(
                f"Unhandled payment status at refresh: {payment_status!r}",
            )

    # ---
    # Cancellation validation specific methods.
    # ---

    def build_cancellation_redirect_url(
        self,
        *,
        pkce_data: PKCEData | None,
    ) -> str | None:
        """Build the redirect URL for the cancellation payload.

        :param pkce_data: The data to build the redirect URL from, if
            relevant.
        :return: The redirect URL.
        """
        if not self.CANCELLATION_REDIRECT_URLS_REQUIRED and self.CANCELLATION_REDIRECT_FLOW in (
            RedirectFlow.OAUTH_AUTHORISATION_CODE,
            RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
        ):
            return None

        kwargs = {}
        if pkce_data is not None:
            kwargs.update(
                {
                    "code_challenge": pkce_data.challenge,
                    "code_challenge_method": pkce_data.method,
                }
            )

        return get_url_with_params(
            self.config["redirect_uri"].get(),
            state=self.get_redirect_state(),
            **kwargs,
        )

    def build_cancellation_error_url(
        self,
        *,
        pkce_data: PKCEData | None,
    ) -> str | None:
        """Build the error redirect URL for the cancellation payload.

        :param pkce_data: The data to build the redirect URL from, if
            relevant.
        :return: The error redirect URL.
        """
        if not self.CANCELLATION_REDIRECT_URLS_REQUIRED and self.CANCELLATION_REDIRECT_FLOW in (
            RedirectFlow.OAUTH_AUTHORISATION_CODE,
            RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
        ):
            return None

        kwargs = {}
        if pkce_data is not None:
            kwargs.update(
                {
                    "code_challenge": pkce_data.challenge,
                    "code_challenge_method": pkce_data.method,
                }
            )

        return get_url_with_params(
            self.config["error_uri"].get(),
            state=self.get_redirect_state(),
            **kwargs,
        )

    def build_cancellation_pkce_data(
        self,
        *,
        challenge_type: PKCEChallengeType,
    ) -> PKCEData:
        """Build PKCE data suitable for the cancellation flow.

        :param challenge_type: The challenge type to use.
        :return: The built PKCE data.
        """
        return PKCEData.build(challenge_type)

    def build_cancellation_supplementary_data(
        self,
        *,
        pkce_data: PKCEData | None,
    ) -> dict[str, Any]:
        """Build supplementary data for when cancelling a payment.

        :param pkce_data: The optional PKCE data to include in report URLs.
        :return: The supplementary data to include as 'supplementaryData'.
        """
        successful_report_url = self.build_cancellation_redirect_url(
            pkce_data=pkce_data,
        )
        unsuccessful_report_url = self.build_cancellation_error_url(
            pkce_data=pkce_data,
        )

        data: dict[str, Any] = {"acceptedAuthenticationApproach": ["REDIRECT"]}
        if successful_report_url is not None:
            data["successfulReportUrl"] = successful_report_url
        if unsuccessful_report_url is not None:
            data["unsuccessfulReportUrl"] = unsuccessful_report_url

        if self.CANCELLATION_INCLUDE_APPLIED_APPROACH:
            page = self.payment_page.stay_or_go(payment_id=self.payment.id)
            data["appliedAuthenticationApproach"] = page.get_applied_approach()

        return data

    def check_current_cancellation_status(self) -> None:
        """Check the cancellation status for the current payment.

        If this method returns, it means that the authorisation is still
        ongoing.

        :raises PaymentInteractionSkipped: The current authorisation is
            finished.
        """
        self.logger.info("Checking the payment cancellation status.")

        data = self.get_payment_status_data()

        if data.status in ("RJCT", "CANC"):
            self.logger.info(
                "The payment is rejected, the cancellation interaction has been skipped."
            )
            raise PaymentInteractionSkipped()
        if data.status not in ("PDNG", "ACSP"):
            self.logger.info(
                "The current payment status is %s, which is not cancellable.",
                data.status,
            )
            raise PaymentCancellationError(
                code=PaymentCancellationErrorCode.NOT_CANCELLABLE,
            )

    def request_cancellation_token(self, *, code: str) -> None:
        """Request an OAuth2 token using an auth code for cancellation.

        This uses the OAuth2 Authorization Code grant.

        :param code: The authorization code to obtain a token with.
        """
        self.logger.info("Requesting an OAuth2 token for cancellation.")

        client_id, client_secret = self.get_oauth_client_credentials()

        headers = {}
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config["redirect_uri"].get(),
        }

        if self.state.pkce_verifier:
            data["code_verifier"] = self.state.pkce_verifier

        if self.OAUTH_CLIENT_AUTH_METHOD == OAuthClientAuthMethod.BASIC:
            basic_auth = b64encode(
                f"{client_id}:{client_secret or ''}".encode("ascii"),
            ).decode("ascii")
            headers["Authorization"] = f"Basic {basic_auth}"
        else:
            data["client_id"] = client_id
            if client_secret is not None:
                data["client_secret"] = client_secret

        self.request_oauth_token(data=data, headers=headers)

    def confirm_payment_cancellation(
        self,
        *,
        psu_auth_factor: str | None,
    ) -> None:
        """Confirm payment cancellation.

        This uses the explicit payment confirmation endpoints provided by the
        standard.

        :param psu_auth_factor: The authentication factor from the PSU,
            if relevant.
        """
        self.logger.info("Confirming the payment cancellation.")

        payload = {}
        if self.state.payment_nonce is not None:
            payload["nonce"] = self.state.payment_nonce
        if psu_auth_factor is not None:
            payload["psuAuthenticationFactor"] = psu_auth_factor

        self.payment_confirmation_page.go(
            payment_id=self.payment.id,
            json=payload,
        )

    def confirm_payment_oauth_cancellation(self) -> None:
        """Confirm the payment cancellation following an OAuth2 flow."""
        self.logger.info("Confirming the payment cancellation following an OAuth2 flow.")

        payload = {}
        if self.state.payment_nonce is not None:
            payload["nonce"] = self.state.payment_nonce

        if (
            self.CANCELLATION_OAUTH_CONFIRMATION_FACTOR == OAuthRedirectFlowConfirmationFactor.CODE
            and self.state.oauth_authorisation_code is not None
        ):
            payload["psuAuthenticationFactor"] = self.state.oauth_authorisation_code
        elif (
            self.CANCELLATION_OAUTH_CONFIRMATION_FACTOR == OAuthRedirectFlowConfirmationFactor.TOKEN
            and self.state.oauth_token is not None
        ):
            payload["psuAuthenticationFactor"] = self.state.oauth_token

        if self.CANCELLATION_COMMON_OAUTH_CONFIRMATION:
            # STET 1.5.0+ style, where the confirmation endpoint is the same
            # for simple and OAuth2 Authorisation Code redirect flows.
            self.payment_confirmation_page.go(
                payment_id=self.payment.id,
                json=payload,
            )
        else:
            # Pre-STET 1.5.0 style, where a dedicated confirmation endpoint
            # is used for OAuth2 Authorisation Code redirect flows.
            self.payment_oauth_confirmation_page.go(
                payment_id=self.payment.id,
                json=payload,
            )

    def detect_cancellation_callback_error(self, callback_url: str) -> None:
        """Detect a cancellation validation callback error.

        This should only raise cancellation errors if an error is detected;
        otherwise, it can safely do nothing.

        Note that this method is only called if
        :py:attr:`UNSAFE_URL_ERROR_DETECTION` is set to ``True``.

        :param callback_url: The URL to detect the cancellation validation
            error from.
        :raises PaymentCancellationError: A cancellation validation error has
            been detected from the URL.
        """
        self.logger.info("Trying to detect an error from the cancellation callback URL.")

        params = get_url_params(callback_url)
        error_code = params.get("error") or params.get("error_code")
        if error_code is not None:
            error_detail = params.get("error_description")
            raise PaymentCancellationError(error_detail or "")
        self.logger.info("There was no error or error_code in the cancellation callback URL.")

    def initialize_payment_cancellation_validation(
        self,
        page: PaymentCancellationPage,
        *,
        pkce_data: PKCEData | None,
    ) -> None:
        """Initialize the payment cancellation validation.

        :param page: The page from which the approach can be determined.
        :param pkce_data: The PKCE data, if relevant.
        :raises BrowserInteraction: An interaction is required from the PSU.
        """
        self.logger.info("Initializing the payment cancellation SCA.")

        data = page.get_validation_data()
        self.state.payment_nonce = data.nonce
        self.state.validation_approach = data.approach

        if data.approach == ValidationApproach.NONE:
            self.logger.info("No validation approach for this cancellation.")

            try:
                self.check_current_cancellation_status()
            except PaymentInteractionSkipped:
                return

            self.logger.error("The cancellation request has been ignored by the ASPSP.")
            raise AssertionError(
                "Our cancellation request seems to have been ignored.",
            )

        if data.approach == ValidationApproach.REDIRECT:
            link = data.links["consentApproval"]

            if self.CANCELLATION_REDIRECT_FLOW in (
                RedirectFlow.SIMPLE,
                RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION,
                RedirectFlow.SIMPLE_WITH_AUTH_FACTOR,
            ):
                self.logger.info(
                    "Raising PaymentRedirect for cancellation with flow %s.",
                    self.CANCELLATION_REDIRECT_FLOW,
                )
                # With the simple redirect flow with confirmation, we do not
                # have a specific status indicating that the cancellation
                # validation requires a confirmation.
                can_skip = (
                    self.CANCELLATION_REDIRECT_FLOW == RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION
                )
                if can_skip:
                    self.logger.info("The payment cancellation callback is skippable.")
                raise PaymentRedirect(link, can_skip=can_skip)

            if self.CANCELLATION_REDIRECT_FLOW not in (
                RedirectFlow.OAUTH_AUTHORISATION_CODE,
                RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
            ):
                self.logger.error(
                    "Unhandled redirect flow at cancellation validation %s",
                    self.CANCELLATION_REDIRECT_FLOW,
                )
                raise NotImplementedError(
                    "Unhandled redirect flow at cancellation validation "
                    + f"initialization: {self.VALIDATION_REDIRECT_FLOW!r}",
                )

            client_id, _ = self.get_oauth_client_credentials()

            code_challenge: str | None = None
            code_challenge_method: str | None = None
            if pkce_data is not None:
                code_challenge = pkce_data.challenge
                code_challenge_method = pkce_data.method

            # The following values should be prevalued by the ASPSP:
            # * 'response_type': prevalued with 'code'.
            # * 'scope': prevalued with 'pisp'.
            # * 'context': prevalued with a hint to the payment-request.
            #
            # We need to complete this URL with our own parameters:
            # * 'state'.
            # * 'redirect_uri', to be used in place of the
            #   'successfulReportUrl' as set in the payment request.
            #
            # We also add PKCE related parameters in case the extension is
            # to be used with the ASPSP.
            self.logger.info(
                "Raising PaymentRedirect for cancellation with flow %s.",
                self.CANCELLATION_REDIRECT_FLOW,
            )
            raise PaymentRedirect(
                get_url_with_params(
                    link,
                    state=self.get_redirect_state(),
                    client_id=client_id,
                    redirect_uri=self.config["redirect_uri"].get(),
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                )
            )

        raise NotImplementedError(f"Unhandled approach {data.approach!r}")

    def resume_payment_cancellation_redirect(self) -> None:
        """Resume payment redirection for a cancellation validation flow.

        :raises BrowserInteraction: An interaction is expected.
        """
        self.logger.info("Resuming the payment cancellation redirect.")

        should_confirm = self.config["confirm"].get()
        if should_confirm:
            if should_confirm == "false":
                # The caller wants to cause a payment validation error.
                # We actually just need to forget the codes we had, and
                # raise an error for the storage to be unusable after a
                # short period of time.
                self.logger.info(
                    "The payment cancellation confirmation was explicitly "
                    "refused by the caller. The payment is thus still alive."
                )
                raise PaymentCancellationError(
                    code=PaymentCancellationErrorCode.CONFIRMATION_REFUSED,
                )

            if self.CANCELLATION_REDIRECT_FLOW in (
                RedirectFlow.OAUTH_AUTHORISATION_CODE,
                RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
            ):
                if (
                    self.state.oauth_token_to_be_requested
                    and self.state.oauth_authorisation_code is not None
                ):
                    self.state.oauth_token_to_be_requested = False

                    # We have actually interrupted ourselves before requesting
                    # a token using the provided OAuth2 authorisation code.
                    # We may do this now.
                    #
                    # Note that if this token request fails because the provided
                    # code was actually invalid, we cannot go back to the PSU
                    # validation anymore; the payment will just fail, and cause
                    # the whole creation and validation to fail.
                    self.request_cancellation_token(
                        code=self.state.oauth_authorisation_code,
                    )

                if self.CANCELLATION_REDIRECT_FLOW == RedirectFlow.OAUTH_AUTHORISATION_CODE:
                    # An additional confirmation request is necessary.
                    self.confirm_payment_oauth_cancellation()

                # We want to reset the token to force requesting a new token on
                # next call, since with some ASPSPs, the token obtained using
                # the OAuth2 Authorisation Code grant cannot be used for other
                # operations such as cancellation.
                self.reset_oauth_token()
            else:
                code = self.state.psu_auth_factor
                self.confirm_payment_cancellation(psu_auth_factor=code)

            # The payment should have been cancelled.
            self.logger.info("The payment cancellation SCA was successfully completed.")
            return

        if self.state.validation_confirmation_required:
            self.logger.info("The payment cancellation confirmation is still required.")
            raise PaymentSameInteraction()

        callback_url = self.config["auth_uri"].get()
        if callback_url is None or not callback_url:
            self.logger.info("A payment callback is expected for this cancellation.")
            raise PaymentSameInteraction()

        if self.CANCELLATION_REDIRECT_FLOW in (
            RedirectFlow.OAUTH_AUTHORISATION_CODE,
            RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION,
        ):
            if callback_url == PaymentRedirect.SKIPPED_VALUE:
                # Oh no, the callback was reported as skippable but it
                # actually isn't! Just fall back on our tracks.
                self.logger.warning(
                    "The callback has been declared skippable with approach "
                    + '"%s" that does not support it.',
                    self.CANCELLATION_REDIRECT_FLOW,
                )
                raise PaymentSameInteraction()

            auth_code = get_url_param(callback_url, "code", default=None)
            if auth_code is None:
                self.logger.info(
                    "Expecting a confirmation code from the callback URL. "
                    "The payment cancellation was seemingly not completed."
                )
                raise PaymentSameInteraction()

            self.state.oauth_authorisation_code = auth_code
            if (
                self.CANCELLATION_REDIRECT_FLOW
                == RedirectFlow.OAUTH_AUTHORISATION_CODE_WITHOUT_CONFIRMATION
                and self.UNSAFE_CONFIRMATIONS
            ):
                self.state.oauth_token_to_be_requested = True
                self.logger.info(
                    "Unsafe confirmation is enabled, the payment cancellation "
                    "confirmation is standing by while waiting for the caller."
                )
                raise PaymentConfirmationRequired()

            # We want to request the validation token now.
            self.request_cancellation_token(code=auth_code)

            if self.CANCELLATION_REDIRECT_FLOW == (RedirectFlow.OAUTH_AUTHORISATION_CODE):
                self.logger.info("Expecting a confirmation for the payment cancellation.")
                raise PaymentConfirmationRequired()

            # The payment cancellation is now confirmed. We want to reset the
            # token to force requesting a new token on next call, since with
            # some ASPSPs, the token obtained using the OAuth2 Authorisation
            # Code grant cannot be used for other operations such as
            # cancellation.
            self.reset_oauth_token()
        elif self.CANCELLATION_REDIRECT_FLOW == (RedirectFlow.SIMPLE_WITH_AUTH_FACTOR):
            if callback_url == PaymentRedirect.SKIPPED_VALUE:
                # Oh no, the callback was reported as skippable but it
                # actually isn't! Just fall back on our tracks.
                self.logger.warning(
                    "The callback has been declared skippable with approach "
                    + '"%s" that does not support it.',
                    self.CANCELLATION_REDIRECT_FLOW,
                )
                raise PaymentSameInteraction()

            psu_auth_factor = get_url_param(
                callback_url,
                "psuAuthenticationFactor",
                default=None,
            )
            if psu_auth_factor is None:
                # Even if there's an error present in the callback URL, we do
                # not want to report the payment cancellation as failed.
                self.logger.info("No psu auth factor in the cancellation callback URL.")
                raise PaymentSameInteraction()

            if self.UNSAFE_CONFIRMATIONS:
                self.state.psu_auth_factor = psu_auth_factor
                self.logger.info(
                    "Unsafe confirmation is enabled, the payment cancellation "
                    "confirmation is standing by while waiting for the caller."
                )
                raise PaymentConfirmationRequired()

            self.confirm_payment_cancellation(psu_auth_factor=psu_auth_factor)
        else:
            # We know the payment cancellation is still awaiting PSU
            # validation here.
            self.logger.info("The payment cancellation is still awaiting PSU validation.")
            raise PaymentSameInteraction()

    def resume_payment_cancellation_validation(self) -> None:
        """Resume the payment cancellation validation.

        :raises BrowserInteraction: An interaction is expected.
        """
        self.logger.info("Resuming the payment cancellation validation.")

        if self.state.first_failure_at is not None:
            # The payment validation was already detected as failed.
            # From here, either we decide still not to report the payment
            # validation as failed to keep waiting for the callback, or
            # we raise a validation error.
            callback_uri: str | None = None
            if "auth_uri" in self.config:
                callback_uri = self.config["auth_uri"].get()

            if self.UNSAFE_URL_ERROR_DETECTION:
                if callback_uri is not None and callback_uri != PaymentRedirect.SKIPPED_VALUE:
                    # We have a callback and are still able to detect errors
                    # from callback URLs, we go for it!
                    # However, note that if we're not able to detect an
                    # error in the callback, we will either keep waiting
                    # or raise a generic validation error.
                    self.detect_cancellation_callback_error(callback_uri)

                if (
                    self.CANCELLATION_FAILURE_CALLBACK_DETECTION_TIMEOUT is not None
                    and self.state.first_failure_at
                    + self.CANCELLATION_FAILURE_CALLBACK_DETECTION_TIMEOUT
                    > datetime.utcnow()
                ):
                    # We're still within the allowed time span, we want to
                    # keep waiting for the next redirect.
                    self.logger.info("Waiting for an cancellation Errback to extract information.")
                    raise PaymentSameInteraction()

            if self.state.first_failure_cancellation_data is not None:
                # The code and message are defined from the first failure!
                # This should always be the case, but the typing can't
                # reflect this reality.
                code, message = self.state.first_failure_cancellation_data
                raise PaymentCancellationError(message or "", code=code)

            raise PaymentCancellationError()

        try:
            self.check_current_cancellation_status()
        except PaymentCancellationError as exc:
            if self.state.validation_approach != ValidationApproach.REDIRECT:
                raise

            # If the current approach is REDIRECT and unsafe URL error
            # detection is enabled, we may want to either delay the
            # validation error reporting or detect from the URL.
            if not self.UNSAFE_URL_ERROR_DETECTION:
                raise

            callback_uri = self.config["auth_uri"].get()
            if callback_uri is not None and callback_uri != PaymentRedirect.SKIPPED_VALUE:
                self.detect_cancellation_callback_error(callback_uri)
                raise

            if self.CANCELLATION_FAILURE_CALLBACK_DETECTION_TIMEOUT is None:
                raise

            self.logger.info(
                "Unsafe URL detection is enabled, waiting for the cancellation Errback."
            )

            # We're keeping in memory that the payment validation has failed,
            # but we do not report it yet, in order to get a callback with
            # more information later. Note that from this point on, we
            # should not be making any more requests to the bank regarding
            # the payment request, we do everything from memory.
            self.state.first_failure_at = datetime.utcnow()
            self.state.first_failure_cancellation_data = (
                exc.code,
                str(exc) or None,
            )

            self.logger.warning(
                "Payment cancellation has failed with code %r and message %r, "
                + "but a callback is expected to determine the exact error "
                + "code before %s",
                exc.code,
                str(exc),
                (
                    self.state.first_failure_at
                    + self.CANCELLATION_FAILURE_CALLBACK_DETECTION_TIMEOUT
                ).isoformat(),
            )
            raise PaymentSameInteraction() from exc
        except PaymentInteractionSkipped:
            # The payment cancellation has already been validated, no need
            # for the rest of this method!
            return

        validation_approach = self.state.validation_approach
        if validation_approach == ValidationApproach.REDIRECT:
            self.resume_payment_cancellation_redirect()
        else:
            self.logger.error(
                "Unhandled cancellation validation approach %s",
                validation_approach,
            )
            raise NotImplementedError(
                "Unhandled cancellation validation approach " + f"{validation_approach!r}",
            )

    def create_payment(self) -> None:
        """Create a payment.

        Make the initial API requests to initiate a payment. An interaction
        (SCA) is expected.

        :raises BrowserInteraction: An interaction is required from the PSU.
        """
        self.logger.info("Creating the payment.")

        # If the instructions have no reference identifiers set, we want
        # to set one to all.
        if self.END_TO_END_IDENTIFIERS_SUPPORTED and any(
            not instruction.reference_id for instruction in self.payment.instructions
        ):
            for instruction, reference_id in zip(
                self.payment.instructions,
                build_end_to_end_identifiers(
                    count=len(self.payment.instructions),
                    length=31,
                ),
            ):
                if not instruction.reference_id:
                    instruction.reference_id = reference_id

        self.state.payment_creation_date = creation_date = datetime.utcnow()
        self.state.payment_information_id = payment_information_id = build_random_identifier()
        self.state.payment_instruction_ids = instruction_ids = tuple(
            build_random_identifier() for _ in self.payment.instructions
        )

        pkce_data = None
        if self.VALIDATION_REDIRECT_WITH_PKCE:
            pkce_data = self.build_validation_pkce_data(
                challenge_type=self.VALIDATION_PKCE_CHALLENGE_TYPE,
            )
            self.state.pkce_verifier = pkce_data.verifier

        payload = self.dialect.build_payload(
            self.payment,
            creation_date=creation_date,
            payment_information_id=payment_information_id,
            instruction_ids=instruction_ids,
        )

        payload["supplementaryData"] = self.build_validation_supplementary_data(pkce_data=pkce_data)

        self.logger.info("Calling the payment creation endpoint.")
        page = self.new_payment_page.go(json=payload)

        self.payment.id = page.get_payment_id()

        self.payment.extra["bank_payment_id"] = self.payment.id
        self.payment.extra["initiation_time"] = creation_date.isoformat()
        self.payment.extra["initiation_request_id"] = page.response.request.headers["X-Request-ID"]

        self.initialize_payment_validation(page, pkce_data=pkce_data)

    # ---
    # Main payment methods.
    # ---

    def create_and_validate_payment(self) -> None:
        """Create and validate a payment."""
        self.check_pre_step()

        if not self.payment.id:
            self.logger.info("Creating the payment with CapOneTimePayment.")
            self.create_payment()
        else:
            try:
                self.resume_payment_validation()
            except ClientError as exc:
                if self.should_retry_with_new_token(exc.response):
                    self.logger.info("Retrying with new token.")
                    self.request_pre_step_client_token()
                    return self.create_and_validate_payment()
                self.logger.exception("ClientError while trying to resume the payment validation")
                raise

        try:
            # Check that the payment has indeed been validated.
            self.check_current_validation_status()
        except PaymentConfirmationRequired as exc:
            self.logger.error(
                "Payment confirmation is still required after having been done already."
            )
            raise AssertionError(
                "Confirmation is still required after having been supposedly "
                + "done already; is the right redirect flow defined?",
            ) from exc
        except PaymentInteractionSkipped:
            pass
        else:
            # The payment is not in a post-validation status, but the
            # procedure for validating the payment is finished; this is
            # not normal!
            self.logger.error(
                "Validation has ended, but payment is not in a post-validation status."
            )
            raise AssertionError(
                "Validation has ended, but payment is not in a "
                + "post-validation status; has an error been ignored?",
            )

        # We want to reset the validation approach, for preparation in case
        # the cancellation method is called later.
        self.reset_validation()

        self.logger.info("The payment validation process has been completed.")

    def check_payment_during_creation(self) -> None:
        """Check the payment while a creation interaction is ongoing."""
        if not self.payment.id:
            # We can't check on the payment if it doesn't exist yet!
            return

        self.logger.info("Checking the payment during creation.")

        self.check_pre_step()
        try:
            self.check_current_validation_status()
        except PaymentConfirmationRequired as exc:
            # If confirmation has not been requested by the module yet,
            # we want to signal that the create & validate method be
            # called for it to be requested.
            if (
                self.is_validation_redirect_skippable
                and not self.state.validation_confirmation_required
            ):
                raise PaymentInteractionSkipped() from exc
        except ClientError as exc:
            if self.should_retry_with_new_token(exc.response):
                self.request_pre_step_client_token()
                return self.check_payment_during_creation()
            raise

    def refresh_payment(self) -> None:
        """Refresh the payment.

        :raises PaymentAccessExpired: The access to the payment has expired.
        """
        if not self.payment.id:
            self.logger.error("Missing payment ID")
            raise AssertionError("Missing payment ID")

        self.logger.info("Refreshing the payment %s.", self.payment.id)

        self.check_pre_step()

        try:
            page = self.payment_page.go(payment_id=self.payment.id)
        except HTTPNotFound as exc:
            if self.RAISE_PAYMENTACCESSEXPIRED_ON_404:
                self.logger.info("Got a 404, and we interpret it as an expired access.")
                raise PaymentAccessExpired() from exc
            self.logger.exception("404 while refreshing the payment")
            raise
        except ClientError as exc:
            if self.should_retry_with_new_token(exc.response):
                self.logger.info("Retrying with a new token.")
                self.request_pre_step_client_token()
                return self.refresh_payment()
            self.logger.exception("ClientError while refreshing the payment")
            raise

        # Update the payment payer.
        if not self.payment.payer:
            self.payment.payer = PaymentAccount()

        page.update_payer(self.payment.payer)

        # Update status data.
        data = page.get_status_data()

        self.payment.extra["last_status"] = data.status
        if data.status_reason:
            self.payment.extra["last_status_reason"] = data.status_reason
        else:
            del self.payment.extra["last_status_reason"]

        for instruction, instruction_data in zip(
            self.payment.instructions,
            page.get_instruction_status_data(),
        ):
            self.set_instruction_status(
                instruction,
                payment_status=data.status,
                payment_status_reason=data.status_reason,
                instruction_status=instruction_data.status,
                instruction_status_reason=instruction_data.status_reason,
            )

    def cancel_payment(self, *, reason: PaymentCancellationReason) -> None:
        """Cancel the payment for a provided reason.

        :param reason: The reason for the cancellation.
        """
        self.logger.info(
            "Cancelling the payment with reason: %s.",
            reason,
        )

        status_reason = self.CANCELLATION_REASON_MAPPING.get(reason)
        if status_reason is None:
            self.logger.error(
                "Cancellation reason %s is not supported by the STET module.",
                reason,
            )
            raise PaymentCancellationError(
                "Unsupported cancellation reason.",
                code=PaymentCancellationErrorCode.OTHER,
            )

        if (
            self.state.payment_information_id is None
            or self.state.payment_creation_date is None
            or self.state.payment_instruction_ids is None
        ):
            # This may occur if we're switching from CapTransfer to
            # CapOneTimePayment, and since this property was stored in the
            # payment object before, and in the browser state now, there
            # is no way to retrieve it as of now.
            self.logger.error("The payment is not cancellable for technical reasons.")
            raise PaymentCancellationError(
                "Unable to cancel the payment due to internal changes.",
                code=PaymentCancellationErrorCode.NOT_CANCELLABLE,
            )

        # We don't want to handle token retries logic here. Since this is
        # an important operation we just recreate the token to be safe.
        self.reset_oauth_token()
        self.check_pre_step()

        try:
            if self.state.validation_approach == ValidationApproach.NONE:
                try:
                    self.check_current_cancellation_status()
                except PaymentInteractionSkipped as exc:
                    raise PaymentCancellationError(
                        code=PaymentCancellationErrorCode.ALREADY_CANCELLED,
                    ) from exc

                pkce_data = None
                self.state.pkce_verifier = None
                if self.CANCELLATION_REDIRECT_WITH_PKCE:
                    pkce_data = self.build_cancellation_pkce_data(
                        challenge_type=self.CANCELLATION_PKCE_CHALLENGE_TYPE,
                    )
                    self.state.pkce_verifier = pkce_data.verifier

                # Cancel the payment.
                payload = self.dialect.build_payload(
                    self.payment,
                    creation_date=self.state.payment_creation_date,
                    payment_information_id=self.state.payment_information_id,
                    instruction_ids=self.state.payment_instruction_ids,
                )

                if self.CANCELLATION_ON_INSTRUCTIONS:
                    for instruction in payload["creditTransferTransaction"]:
                        instruction.update(
                            {
                                "transactionStatus": self.CANCELLATION_STATUS,
                                "statusReasonInformation": status_reason,
                            }
                        )

                payload.update(
                    {
                        "paymentInformationStatus": self.CANCELLATION_STATUS,
                        "statusReasonInformation": status_reason,
                        "supplementaryData": self.build_cancellation_supplementary_data(
                            pkce_data=pkce_data,
                        ),
                    }
                )

                page = self.payment_page.go(
                    payment_id=self.payment.id,
                    json=payload,
                    method="PUT",
                )

                self.payment.extra["cancellation_time"] = now_as_utc().isoformat()
                self.payment.extra["cancellation_request_id"] = page.response.request.headers[
                    "X-Request-ID"
                ]

                self.initialize_payment_cancellation_validation(
                    page,
                    pkce_data=pkce_data,
                )
            else:
                self.resume_payment_cancellation_validation()

            # We want to check that the cancellation flow has indeed finished
            # here.
            try:
                self.check_current_cancellation_status()
            except PaymentInteractionSkipped:
                # That's great news!
                pass
            else:
                # The cancellation should have been effective, but it seems
                # as though the payment is not cancelled yet.
                raise AssertionError(
                    "Cancellation has ended, but payment is not in a "
                    + "cancelled status; has an error been ignored?",
                )
        except BrowserInteraction:
            # Is an interaction, which means the cancellation is still ongoing.
            raise
        except Exception:
            # Could be a failure or a bug, which means we no longer consider
            # the cancellation to be ongoing.
            self.logger.exception(
                "Got exception while checking for the cancellation. We abort the cancellation flow."
            )
            self.reset_validation()
            raise

        self.logger.info("The payment cancellation flow has been completed.")

    def check_payment_during_cancellation(self) -> None:
        """Check on the payment while a cancellation is ongoing auth."""
        self.logger.info("Checking on the payment during cancellation.")

        self.check_pre_step()
        try:
            self.check_current_cancellation_status()
        except ClientError as exc:
            if self.should_retry_with_new_token(exc.response):
                self.request_pre_step_client_token()
                return self.check_payment_during_cancellation()
            raise


class Stet141PaymentBrowser(Stet140PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.4.1.3 of the STET PSD2 API
    standard, published on 2019-01-15 by Hervé Robache.
    """

    VERSION = "1.4.1.3"


class Stet142PaymentBrowser(Stet141PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.4.2.17 of the STET PSD2 API
    standard, published on 2019-11-25 by Hervé Robache.
    """

    VERSION = "1.4.2.17"

    DIALECT = Stet142PaymentDialect

    CANCELLATION_STATUS = "CANC"
    CANCELLATION_REASON_MAPPING = {
        PaymentCancellationReason.ORDERED_BY_PSU: "DS02",
        PaymentCancellationReason.DUPLICATE: "DUPL",
        PaymentCancellationReason.FRAUDULENT: "FRAD",
        PaymentCancellationReason.TECHNICAL: "TECH",
    }


class Stet150PaymentBrowser(Stet142PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.5.0.43 of the STET PSD2 API
    standard, published on 2020-10-30 by Hervé Robache.
    """

    VERSION = "1.5.0.43"

    DIALECT = Stet150PaymentDialect

    # Accepted roles for the different redirect flows switch around with
    # STET 1.5.0, with Enforced Redirect becoming the default flow.
    VALIDATION_REDIRECT_FLOW: ClassVar[RedirectFlow] = RedirectFlow.OAUTH_AUTHORISATION_CODE

    # The '/o-confirmation' endpoint is no longer specified in STET 1.5.0.
    VALIDATION_COMMON_OAUTH_CONFIRMATION = True
    CANCELLATION_COMMON_OAUTH_CONFIRMATION = True


class Stet151PaymentBrowser(Stet150PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.5.1.6 of the STET PSD2 API
    standard, published on 2021-04-12 by Hervé Robache.
    """

    VERSION = "1.5.1.6"

    DIALECT = Stet151PaymentDialect


class Stet162PaymentBrowser(Stet151PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.6.2.0 of the STET PSD2 API
    standard, published on 2022-06-13 by Hervé Robache.
    """

    VERSION = "1.6.2.0"

    DIALECT = Stet162PaymentDialect


class Stet163PaymentBrowser(Stet162PaymentBrowser):
    """Basic STET payment browser.

    This browser has been built against version 1.6.3.1 of the STET PSD2 API
    standard, published on 2022-10-03 by Hervé Robache.
    """

    VERSION = "1.6.3.1"

    DIALECT = Stet163PaymentDialect
