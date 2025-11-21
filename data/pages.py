import re

from woob.browser.elements import method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanDecimal, Date, Field, Format
from woob.capabilities.bank import Account
from woob.capabilities.bank.base import BalanceType
from woob.capabilities.bank.transfer import TransferStatus
from woob.capabilities.base import NotAvailable, empty, find_object
from woob.tools.capabilities.bank.transactions import FrenchTransaction
from woob_modules.stet.pages import (
    AccountsPage as _AccountsPage,
    BalancesPage as _BalancesPage,
    PaymentRequestPage as _PaymentRequestPage,
    TransactionsPage as _TransactionsPage,
)


class Transaction(FrenchTransaction):
    PATTERNS = [
        (
            # Different type of ORDER, where we don't want to pick the date
            # in the label because it is the date of start of the loan started.
            re.compile(r"^(?P<category>(PRELEVEMENT|PRELEVT|PRELEVMNT)) INITIATIVE (?P<text>.*)$"),
            FrenchTransaction.TYPE_LOAN_PAYMENT,
        ),
        (
            re.compile(r"PRELEVEMENT CARTE DEPENSES CARTE AU .*"),
            FrenchTransaction.TYPE_CARD_SUMMARY,
        ),
        (
            # We can get something like :
            # PRELEVEMENT ********************* **** *** **** 12 34 5678 XXXXXXX XXXX FRXXXXXXXXXXXXX
            re.compile(
                r"^(?P<category>(PRELEVEMENT|PRELEVT|PRELEVMNT)) (?P<text>.*)(?<!\W\d{4}) (?P<dd>0[1-9]|[12]\d|3[01])\s(?P<mm>0[1-9]|1[0-2])\s(?P<yy>20\d{2})(?:$|\s.*)"
            ),
            FrenchTransaction.TYPE_ORDER,
        ),
        (
            re.compile(r"^(?P<category>(PRELEVEMENT|PRELEVT|PRELEVMNT)) (?P<text>.*)$"),
            FrenchTransaction.TYPE_ORDER,
        ),
        (
            re.compile(
                r"^(?P<category>(PRELEVEMENT|PRELEVT|PRELEVMNT)) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) .*"
            ),
            FrenchTransaction.TYPE_ORDER,
        ),
        (
            re.compile(
                r"^(?P<category>(PRELEVEMENT|PRELEVT|PRELEVMNT)) (?P<text>.*) (?P<dd>\d{2})-(?P<mm>0[1-9]|1[012])$"
            ),
            FrenchTransaction.TYPE_ORDER,
        ),
        (
            re.compile(r"^(?P<category>VIREMENT (EMIS|EN VOTRE FAVEUR)) (?P<text>.*)$"),
            FrenchTransaction.TYPE_TRANSFER,
        ),
        (
            re.compile(
                r"^(?P<category>REMBOURSEMENT DE PRET) (?P<text>.*) ECHEANCE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2,4})$"
            ),
            FrenchTransaction.TYPE_LOAN_PAYMENT,
        ),
        (
            re.compile(r"^(?P<category>REMBOURSEMENT DE PRET) (?P<text>.*)$"),
            FrenchTransaction.TYPE_LOAN_PAYMENT,
        ),
        (
            re.compile(
                r"^(?P<category>VERSEMENT D'ESPECES) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) .*"
            ),
            FrenchTransaction.TYPE_CASH_DEPOSIT,
        ),
        (
            re.compile(
                r"""^(?P<category>RETRAIT (AU DISTRIBUTEUR|MUR D'ARGENT)) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2}) .*"""
            ),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(
                r"^(?P<category>PAIEMENT PAR CARTE) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})$"
            ),
            FrenchTransaction.TYPE_CARD,
        ),
        (re.compile(r"^CARTE (?P<text>.*)$"), FrenchTransaction.TYPE_DEFERRED_CARD),
        (
            re.compile(r"^(?P<text>(?P<category>AVOIR) .*) (?P<dd>\d{2})/(?P<mm>(0[1-9]|1[0-2]))$"),
            FrenchTransaction.TYPE_PAYBACK,
        ),
        (re.compile(r""".*REMISE  D'EFFETS.*"""), FrenchTransaction.TYPE_PAYBACK),
        (re.compile(r".*REMISE CARTE.*"), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r".*COTISATION.*"), FrenchTransaction.TYPE_BANK),
        (re.compile(r".*FRAIS.*"), FrenchTransaction.TYPE_BANK),
        (re.compile(r".*INTERETS CREDITEURS.*"), FrenchTransaction.TYPE_BANK),
        (re.compile(r".*CHEQUE EMIS.*"), FrenchTransaction.TYPE_CHECK),
        (re.compile(r".*REMISE DE CHEQUE.*"), FrenchTransaction.TYPE_DEPOSIT),
    ]


class AccountsPage(_AccountsPage):
    BALANCE_TYPES = {
        "CLBD": BalanceType.CLOSING,
    }

    @method
    class iter_accounts(_AccountsPage.iter_accounts.klass):
        # There can be duplicates based on iban, we must ignore them
        ignore_duplicate = True

        class item(_AccountsPage.iter_accounts.klass.item):
            def condition(self):
                """
                Do not process cards without card number, else we
                can not match direct access cards and label
                """
                if Field("type")(self) == Account.TYPE_CARD and not Dict(
                    "accountId/other/cardNumber", default=None
                )(self):
                    self.logger.warning("Card without number, not processed")
                    return False
                return True

            def obj_id(self):
                """
                There is no account numbers sent by the stet API, but the direct access send them.
                To match direct access accounts number we:
                 - use the cardNumber for cards, it matches direct access webid
                 - extract account number from IBAN for checking accounts
                """
                acc_type = Field("type")(self)
                if acc_type == Account.TYPE_CARD:
                    acc_id = Dict("accountId/other/cardNumber")(self)
                elif acc_type == Account.TYPE_CHECKING:
                    # get account number from iban
                    acc_id = Field("iban")(self)[-13:-2]
                else:
                    raise AssertionError('Unhandled account type "%s"' % acc_type)

                return acc_id

            obj_number = obj_id

            def obj_label(self):
                """Label needs some customization to match direct access labels"""
                acc_type = Field("type")(self)
                if acc_type == Account.TYPE_CARD:
                    label = Format("Carte %s %s", Field("number"), Dict("name"))(self)
                elif acc_type == Account.TYPE_CHECKING:
                    product = Dict("product", default=None)(self) or "Compte"
                    label = Dict("name")(self)
                    if not label.startswith(product):
                        label = "%s %s" % (product, label)
                else:
                    raise AssertionError('Unhandled account type "%s"' % acc_type)

                return label

            # Needed as an id for the different api routes
            obj__resource_id = Dict("resourceId")

            # Needed for consent if present
            obj__area = Dict("accountId/area", default=NotAvailable)
            obj__area_id = Dict("accountId/area/areaId", default=NotAvailable)
            obj__area_label = Dict("accountId/area/areaLabel", default=NotAvailable)

            # Needed for card consent
            obj__other_identification = Dict("accountId/other/identification", default=NotAvailable)
            obj__other_scheme_name = Dict("accountId/other/schemeName", default=NotAvailable)
            obj__other_issuer = Dict("accountId/other/issuer", default=NotAvailable)

            def obj_parent(self):
                if Field("type")(self) == Account.TYPE_CARD:
                    parent_id = Dict("linkedAccount")(self)
                    return find_object(self.parent.objects.values(), iban=parent_id)

            def obj_balance(self):
                # not done in /balances to save on requests
                # (low limit before triggering 'too many requests' error)
                balances = Dict("balances", default=None)(
                    self
                )  # no balance present before consenting account
                if balances:
                    for balance in balances:
                        if (
                            balance["balanceType"] == "CLBD"
                            and "Accounting Balance" in balance["name"]
                        ):
                            return CleanDecimal(Dict("balanceAmount/amount"))(balance)
                return NotAvailable

            def obj_coming(self):
                # not done in /balances to save on requests
                # (low limit before triggering 'too many requests' error)
                coming = None
                balances = Dict("balances", default=None)(
                    self
                )  # no balance present before consenting account
                if balances:
                    for balance in balances:
                        if (
                            balance["balanceType"] == "XPCD"
                            and "Instant Balance" in balance["name"]
                        ):
                            amount = CleanDecimal(Dict("balanceAmount/amount"))(balance)
                            # 2 coming balances available, one for the end of current debit period + one for the next period,
                            # even if 0 EUR --> add them up
                            if coming:
                                coming += amount
                            else:
                                coming = amount

                if empty(coming):
                    return NotAvailable
                else:
                    return coming

            class obj_all_balances(_BalancesPage.fill_balances.klass.obj_all_balances):
                def condition(self):
                    # If the accounts have not yet been consented, the 'balances' json key is empty
                    return Dict("balances")(self)

                class item(_BalancesPage.fill_balances.klass.obj_all_balances.item):
                    # Since /balances is not handled on cragr balances are returned on /accounts
                    # They work the same, so we can inherit obj_all_balances on AccountsPage
                    pass


class TransactionsPage(_TransactionsPage):
    @method
    class iter_transactions(_TransactionsPage.iter_transactions.klass):
        class item(_TransactionsPage.iter_transactions.klass.item):
            klass = Transaction

            # Use rdate when provided in 'transactionDate' (card accounts' transactions only);
            obj_rdate = Date(Dict("transactionDate", default=None), default=NotAvailable)
            obj_vdate = Date(Dict("valueDate", default=None), default=NotAvailable)

            def validate(self, obj):
                if self.env["account_type"] == Account.TYPE_CARD:
                    # those are the monthly fees for the card usage
                    # it already is on the parent account history, next to card summary
                    return "FRAIS CARTE" not in obj.label
                return True


class PaymentRequestPage(_PaymentRequestPage):
    def get_transfer(self):
        transfer = super().get_transfer()

        # The bank API might return a payment in the "PART" status (mapped to
        # ACTIVE) despite all of its individual instructions being in the
        # "done" or "rejected" status.
        # If this is the case we fix the payment status to reflect the actual
        # status of its instructions.
        if transfer.status == TransferStatus.ACTIVE:
            instructions_states = {instruction.status for instruction in transfer.instructions}
            if instructions_states == {TransferStatus.DONE}:
                transfer.status = TransferStatus.DONE
            elif instructions_states == {TransferStatus.CANCELLED}:
                transfer.status = TransferStatus.CANCELLED
                first_instruction = transfer.instructions[0]
                if first_instruction.cancelled_exception:
                    transfer.cancelled_exception = first_instruction.cancelled_exception
            else:
                transfer.status = TransferStatus.SCHEDULED

        return transfer
