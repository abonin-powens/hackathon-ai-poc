"""Page definitions for payment browsers for STET."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText, Coalesce, Map, Type
from woob.browser.pages import JsonPage, RawPage
from woob.capabilities.payment import PaymentAccount

from .utils import (
    OAuthTokenData,
    PaymentStatusData,
    StetException,
    ValidationApproach,
    ValidationData,
)


class ErrorPage(RawPage):
    """Generic error page for STET APIs."""

    def raise_if_basic_error_found(self) -> None:
        """Raise an exception if we manage to find a basic error.

        This error scheme is actually not found within the standard, but
        within the Swagger files provided with it. An example error using
        this scheme is the following::

            {
                "timestamp": "2018-03-30T16:06:27.499+0000",
                "status": 400,
                "error": "Bad Request",
                "message": "Missing request header 'Digest' for method parameter of type String",
                "path": "/v1/accounts"
            }

        :raises StetError: An error has been found.
        """
        try:
            doc = self.response.json()
            title = Coalesce(
                Dict("error", default=None),
                Dict("errorCode", default=None),
            )(doc)
            detail = Coalesce(
                Dict("message", default=None),
                Dict("errorDescription", default=None),
            )(doc)
        except Exception:
            return

        raise StetException(
            title=title,
            detail=detail,
            response=self.response,
        )

    def raise_if_error_found(self) -> None:
        """Raise an exception if we manage to find an error to raise."""
        self.raise_if_basic_error_found()


class OAuthTokenPage(JsonPage):
    """Page containing token data."""

    def get_token_data(self) -> OAuthTokenData:
        """Get the obtained OAuth2 token data."""
        expires_at = None
        expires_in = Type(
            Dict("expires_in", default=None),
            type=int,
            default=None,
        )(self.doc)
        if expires_in is not None:
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        return OAuthTokenData(
            token=Dict("access_token")(self.doc),
            token_type=Dict("token_type", default=None)(self.doc),
            expires_at=expires_at,
            refresh_token=Dict("refresh_token", default=None)(self.doc),
        )


class PaymentOperationPage(JsonPage):
    """Base page with payment operation validation data.

    This includes payment initiation and cancellation pages.
    """

    def get_links(self) -> dict[str, str]:
        """Get links.

        Note that this takes into account the fact that links can be both
        direct and as objects with an 'href' key.

        :return: A dictionary of (key, url).
        """
        links = {}

        base = self.response.url
        for key, link in self.doc.get("_links", {}).items():
            if isinstance(link, dict):
                url = link["href"]
            elif isinstance(link, str):
                url = link
            elif link is None:
                continue
            else:
                raise AssertionError(f"Unknown format for link {link!r}")

            links[key] = urljoin(base, url)

        return links

    def get_validation_data(self) -> ValidationData:
        """Get the current validation data."""
        return ValidationData(
            approach=Map(
                Dict("appliedAuthenticationApproach", default="NONE"),
                {
                    "NONE": ValidationApproach.NONE,
                    "REDIRECT": ValidationApproach.REDIRECT,
                    "DECOUPLED": ValidationApproach.DECOUPLED,
                    "EMBEDDED-1-FACTOR": ValidationApproach.EMBEDDED,
                },
            )(self.doc),
            nonce=Dict("nonce", default=None)(self.doc),
            links=self.get_links(),
        )


class NewPaymentPage(PaymentOperationPage):
    """Page containing payment creation data."""

    PAYMENT_LOCATION_RE = re.compile(r"payment-requests/([^/]+)$")

    def get_payment_id(self) -> str:
        """Get the payment identifier.

        Note that the approach employed here is implicit and vaguely described
        in STET v1.4.2 section 4.10.3:

        > The PISP asks to retrieve the Payment/Transfer Request that has been
        > saved by the ASPSP. The PISP uses the location link provided by the
        > ASPSP in response of the posting of this request.

        The 'Location' header has a varying base: sometimes we find
        'payment-requests/xyz', sometimes '/payment-requests/xyz', sometimes
        the relative URL to the root, sometimes the complete URL.
        In any case, we want to use the most general result.

        :return: The obtained payment identifier from the page.
        """
        location = self.response.headers["Location"]
        match = self.PAYMENT_LOCATION_RE.search(location)
        if match is None:
            raise ValueError("Unable to find the payment identifier.")

        return match.group(1)


class PaymentPage(JsonPage):
    """Page containing payment data."""

    def get_status_data(self) -> PaymentStatusData:
        """Get status data regarding the current payment."""
        return PaymentStatusData(
            status=CleanText(
                Dict("paymentRequest/paymentInformationStatus", default=None),
                default=None,
            )(self.doc)
            or None,
            status_reason=CleanText(
                Dict("paymentRequest/statusReasonInformation", default=None),
                default=None,
            )(self.doc)
            or None,
        )

    def get_applied_approach(self) -> str:
        """Get the currently available applied authentication approach.

        :return: The approach.
        :raises ValueError: No such value is present on the page.
        """
        return CleanText(
            Dict(
                "paymentRequest/supplementaryData/appliedAuthenticationApproach",
            )
        )(self.doc)

    def update_payer(self, payer: PaymentAccount) -> None:
        """Update the payer with what is in the response."""
        payer_holder_name = CleanText(
            Dict("paymentRequest/debtor/name", default=None),
            default=None,
        )(self.doc)
        payer_iban = CleanText(
            Dict("paymentRequest/debtorAccount/iban", default=None),
            default=None,
        )(self.doc)

        if payer_holder_name:
            payer.holder_name = payer_holder_name
        if payer_iban:
            payer.iban = payer_iban

    def get_instruction_status_data(self) -> list[PaymentStatusData]:
        """Get status data regarding the instructions."""
        return [
            PaymentStatusData(
                status=CleanText(
                    Dict("transactionStatus", default=None),
                    default=None,
                )(section)
                or None,
                status_reason=CleanText(
                    Dict("statusReasonInformation", default=None),
                    default=None,
                )(section)
                or None,
            )
            for section in Dict("paymentRequest/creditTransferTransaction")(self.doc)
        ]


class PaymentCancellationPage(PaymentOperationPage):
    """Page containing cancellation validation data."""
