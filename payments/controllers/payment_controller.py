import json

from urllib.parse import urlencode

from requests.exceptions import HTTPError

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.base_document import get_controller
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.desk.form.load import get_automatic_email_link, get_document_email
from payments.payments.doctype.payment_session_log.payment_session_log import (
	create_log,
)
from frappe.utils import get_url

from payments.utils import PAYMENT_SESSION_REF_KEY

from payments.exceptions import (
	FailedToInitiateFlowError,
	PayloadIntegrityError,
	PaymentControllerProcessingError,
	RefDocHookProcessingError,
)

from payments.types import (
	Initiated,
	TxData,
	_Processed,
	Processed,
	PSLName,
	PaymentUrl,
	PaymentMandate,
	SessionType,
	Proceeded,
	RemoteServerInitiationPayload,
	GatewayProcessingResponse,
	SessionStates,
	FrontendDefaults,
	ActionAfterProcessed,
)

from typing import TYPE_CHECKING, Optional, overload
from payments.payments.doctype.payment_session_log.payment_session_log import (
	PaymentSessionLog,
)

if TYPE_CHECKING:
	from payments.payments.doctype.payment_gateway.payment_gateway import PaymentGateway


def _error_value(error, flow):
	return _(
		"Our server had an issue processing your {0}. Please contact customer support mentioning: {1}"
	).format(flow, error)


def _help_me_develop(state):
	from pprint import pprint

	print("self.state: ")
	pprint(state)


class PaymentController(Document):
	"""This controller implemets the public API of payment gateway controllers."""

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		frontend_defaults: FrontendDefaults
		flowstates: SessionStates

	def __new__(cls, *args, **kwargs):
		assert hasattr(cls, "flowstates") and isinstance(
			cls.flowstates, SessionStates
		), """the controller must declare its flow states in `cls.flowstates`
		and it must be an instance of payments.types.SessionStates
		"""
		assert hasattr(cls, "frontend_defaults") and isinstance(
			cls.frontend_defaults, FrontendDefaults
		), """the controller must declare its flow states in `cls.frontend_defaults`
		and it must be an instance of payments.types.FrontendDefaults
		"""
		return super().__new__(cls)

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.state = frappe._dict()

	@overload
	@staticmethod
	def initiate(
		tx_data: TxData, gateway: str, correlation_id: str | None, name: str | None
	) -> PSLName:
		...

	@staticmethod
	def initiate(
		tx_data: TxData,
		gateway: "PaymentController" = None,
		correlation_id: str = None,
		name: str = None,
	) -> ("PaymentController", PSLName):
		"""Initiate a payment flow from Ref Doc with the given gateway.

		Inheriting methods can invoke super and then set e.g. correlation_id on self.state.psl to save
		and early-obtained correlation id from the payment gateway or to initiate the user flow if delegated to
		the controller (see: is_user_flow_initiation_delegated)
		"""
		if isinstance(gateway, str):
			payment_gateway: PaymentGateway = frappe.get_cached_doc("Payment Gateway", gateway)

			if not payment_gateway.gateway_controller and not payment_gateway.gateway_settings:
				frappe.throw(
					_(
						"{0} is not fully configured, both Gateway Settings and Gateway Controller need to be set"
					).format(gateway)
				)

			self = frappe.get_cached_doc(
				payment_gateway.gateway_settings,
				payment_gateway.gateway_controller or payment_gateway.gateway_settings,  # may be a singleton
			)
		else:
			self = gateway

		self.validate_tx_data(tx_data)  # preflight check

		psl = create_log(
			tx_data=tx_data,
			controller=self,
			status="Created",
		)
		return self, psl.name

	@staticmethod
	def get_payment_url(psl_name: PSLName) -> PaymentUrl | None:
		"""Use the payment url to initiate the user flow, for example via email or chat message.

		Beware, that the controller might not implement this and in that case return: None
		"""
		params = {
			PAYMENT_SESSION_REF_KEY: psl_name,
		}
		return get_url(f"./pay?{urlencode(params)}")

	@staticmethod
	def pre_data_capture_hook(psl_name: PSLName) -> dict:
		"""Call this before presenting the user with a form to capture additional data.

		Implementation is optional, but can be used to acquire any additonal data from the remote
		gateway that should be present already during data capture.
		"""

		psl: PaymentSessionLog = frappe.get_cached_doc("Payment Session Log", psl_name)
		self: "PaymentController" = psl.get_controller()
		data = self._pre_data_capture_hook()
		psl.update_gateway_specific_state(data, "Data Capture")
		return data

	@staticmethod
	def proceed(psl_name: PSLName, updated_tx_data: TxData = None) -> Proceeded:
		"""Call this when the user agreed to proceed with the payment to initiate the capture with
		the remote payment gateway.

		If the capture is initialized by the gatway, call this immediatly without waiting for the
		user OK signal.

		updated_tx_data:
		   Pass any update to the inital transaction data; this can reflect later customer choices
		   and thereby modify the flow

		Example:
		```python
		if controller.is_user_flow_initiation_delegated():
		        controller.proceed()
		else:
		        # example (depending on the doctype & business flow):
		        # 1. send email with payment link
		        # 2. let user open the link
		        # 3. upon rendering of the page: call proceed; potentially with tx updates
		        pass
		```
		"""

		psl: PaymentSessionLog = frappe.get_cached_doc("Payment Session Log", psl_name)
		self: "PaymentController" = psl.get_controller()

		psl.update_tx_data(updated_tx_data or {}, "Started")  # commits

		self.state = psl.load_state()
		# controller specific temporary modifications
		self.state.tx_data = self._patch_tx_data(self.state.tx_data)
		self.state.mandate: PaymentMandate = self._get_mandate()

		try:
			frappe.flags.integration_request_doc = psl  # for linking error logs

			if self._should_have_mandate() and not self.mandate:
				self.state.mandate = self._create_mandate()
				initiated = self._initiate_mandate_acquisition()
				psl.db_set(
					{
						"processing_response_payload": None,  # in case of a reset
						"flow_type": SessionType.mandate_acquisition,
						"correlation_id": initiated.correlation_id,
						"mandate": f"{self.state.mandate.doctype}[{self.state.mandate.name}]",
					},
					# commit=True,
				)
				psl.set_initiation_payload(initiated.payload, "Initiated")  # commits
				return Proceeded(
					integration=self.doctype,
					psltype=SessionType.mandate_acquisition,
					mandate=self.state.mandate,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)
			elif self.state.mandate:
				initiated = self._initiate_mandated_charge()
				psl.db_set(
					{
						"processing_response_payload": None,  # in case of a reset
						"flow_type": SessionType.mandated_charge,
						"correlation_id": initiated.correlation_id,
						"mandate": f"{self.state.mandate.doctype}[{self.state.mandate.name}]",
					},
					# commit=True,
				)
				psl.set_initiation_payload(initiated.payload, "Initiated")  # commits
				return Proceeded(
					integration=self.doctype,
					psltype=SessionType.mandated_charge,
					mandate=self.state.mandate,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)
			else:
				initiated = self._initiate_charge()
				psl.db_set(
					{
						"processing_response_payload": None,  # in case of a reset
						"flow_type": SessionType.charge,
						"correlation_id": initiated.correlation_id,
					},
					# commit=True,
				)
				psl.set_initiation_payload(initiated.payload, "Initiated")  # commits
				return Proceeded(
					integration=self.doctype,
					psltype=SessionType.charge,
					mandate=None,
					txdata=self.state.tx_data,
					payload=initiated.payload,
				)

		# some gateways don't return HTTP errors ...
		except FailedToInitiateFlowError as e:
			psl.set_initiation_payload(e.data, "Error")
			error = psl.log_error(title=e.message)
			frappe.redirect_to_message(
				_("Payment Gateway Error"),
				_("Please contact customer care mentioning: {0} and {1}").format(psl, error),
				http_status_code=401,
				indicator_color="yellow",
			)
			raise frappe.Redirect

		# ... yet others do ...
		except HTTPError as e:
			data = frappe.flags.integration_request.json()
			psl.set_initiation_payload(data, "Error")
			error = frappe.get_last_doc("Error Log")
			frappe.redirect_to_message(
				_("Payment Gateway Error"),
				_("Please contact customer care mentioning: {0} and {1}").format(psl, error),
				http_status_code=401,
				indicator_color="yellow",
			)
			raise frappe.Redirect

		except Exception as e:
			error = psl.log_error(title="Unknown Initialization Failure")
			frappe.redirect_to_message(
				_("Payment Gateway Error"),
				_("Please contact customer care mentioning: {0}").format(error),
				http_status_code=401,
				indicator_color="yellow",
			)
			raise frappe.Redirect

	def __process_response(
		self,
		psl: PaymentSessionLog,
		response: GatewayProcessingResponse,
		ref_doc: Document,
		callable,
		hookmethod,
		psltype,
	) -> Processed | None:
		processed = None
		try:
			processed = callable()  # idempotent on second run
		except Exception:
			raise PaymentControllerProcessingError(f"{callable} failed", psltype)

		assert self.flags.status_changed_to in (
			self.flowstates.success
			+ self.flowstates.pre_authorized
			+ self.flowstates.processing
			+ self.flowstates.declined
		), "self.flags.status_changed_to must be in the set of possible states for this controller:\n - {}".format(
			"\n - ".join(
				self.flowstates.success
				+ self.flowstates.pre_authorized
				+ self.flowstates.processing
				+ self.flowstates.declined
			)
		)

		ret = {
			"status_changed_to": self.flags.status_changed_to,
			"payload": response.payload,
		}

		changed = False

		if self.flags.status_changed_to in self.flowstates.success:
			changed = "Paid" != psl.status
			psl.db_set("decline_reason", None)
			psl.set_processing_payload(response, "Paid")  # commits
			ret["indicator_color"] = "green"
			processed = processed or Processed(
				message=_("{} succeeded").format(psltype.title()),
				action=dict(href="/", label=_("Go to Homepage")),
				**ret,
			)
		elif self.flags.status_changed_to in self.flowstates.pre_authorized:
			changed = "Authorized" != psl.status
			psl.db_set("decline_reason", None)
			psl.set_processing_payload(response, "Authorized")  # commits
			ret["indicator_color"] = "green"
			processed = processed or Processed(
				message=_("{} authorized").format(psltype.title()),
				action=dict(href="/", label=_("Go to Homepage")),
				**ret,
			)
		elif self.flags.status_changed_to in self.flowstates.processing:
			changed = "Processing" != psl.status
			psl.db_set("decline_reason", None)
			psl.set_processing_payload(response, "Processing")  # commits
			ret["indicator_color"] = "yellow"
			processed = processed or Processed(
				message=_("{} awaiting further processing by the bank").format(psltype.title()),
				action=dict(href="/", label=_("Refresh")),
				**ret,
			)
		elif self.flags.status_changed_to in self.flowstates.declined:
			changed = "Declined" != psl.status
			psl.db_set(
				{
					"decline_reason": self._render_failure_message(),
					"button": None,  # reset the button for another chance
				}
			)
			psl.set_processing_payload(response, "Declined")  # commits
			ret["indicator_color"] = "red"
			incoming_email = None
			if automatic_linking_email := get_automatic_email_link():
				incoming_email = get_document_email(
					self.state.tx_data.reference_doctype,
					self.state.tx_data.reference_docname,
				)
			if incoming_email := incoming_email or EmailAccount.find_default_incoming():
				subject = _("Payment declined for: {}").format(self.state.tx_data.reference_docname)
				body = _("Please help me with ref '{}'").format(psl.name)
				href = "mailto:{incoming_email.email_id}?subject={subject}"
				action = dict(href=href, label=_("Email Us"))
			else:
				action = dict(href=self.get_payment_url(psl.name), label=_("Refresh"))
			processed = processed or Processed(
				message=_("{} declined").format(psltype.title()),
				action=action,
				**ret,
			)

		try:
			ref_doc.flags.payment_session = frappe._dict(
				changed=changed, state=self.state, flags=self.flags, flowstates=self.flowstates
			)  # when run as server script: can only set flags
			res = ref_doc.run_method(
				hookmethod,
				changed,
				self.state,
				self.flags,
				self.flowstates,
			)
			# result from server script run
			res = ref_doc.flags.payment_result or res
			if res:
				# type check the result value on user implementations
				res["action"] = ActionAfterProcessed(**res.get("action", {})).__dict__
				_res = _Processed(**res)
				processed = Processed(**(ret | _res.__dict__))
		except Exception as e:
			raise RefDocHookProcessingError(psltype) from e

		return processed

	def _process_response(
		self, psl: PaymentSessionLog, response: GatewayProcessingResponse, ref_doc: Document
	) -> Processed:
		self._validate_response()

		match psl.flow_type:
			case SessionType.mandate_acquisition:
				self.state.mandate: PaymentMandate = self._get_mandate()
				processed: Processed = self.__process_response(
					psl=psl,
					response=response,
					ref_doc=ref_doc,
					callable=self._process_response_for_mandate_acquisition,
					hookmethod="on_payment_mandate_acquisition_processed",
					psltype="mandate adquisition",
				)
			case SessionType.mandated_charge:
				self.state.mandate: PaymentMandate = self._get_mandate()
				processed: Processed = self.__process_response(
					psl=psl,
					response=response,
					ref_doc=ref_doc,
					callable=self._process_response_for_mandated_charge,
					hookmethod="on_payment_mandated_charge_processed",
					psltype="mandated charge",
				)
			case SessionType.charge:
				processed: Processed = self.__process_response(
					psl=psl,
					response=response,
					ref_doc=ref_doc,
					callable=self._process_response_for_charge,
					hookmethod="on_payment_charge_processed",
					psltype="charge",
				)

		return processed

	@staticmethod
	def process_response(psl_name: PSLName, response: GatewayProcessingResponse) -> Processed:
		"""Call this from the controlling business logic; either backend or frontend.

		It will recover the correct controller and dispatch the correct processing based on data that is at this
		point already stored in the integration log

		payload:
		    this is a signed, sensitive response containing the payment status; the signature is validated prior
		    to processing by controller._validate_response
		"""

		psl: PaymentSessionLog = frappe.get_cached_doc("Payment Session Log", psl_name)
		self: "PaymentController" = psl.get_controller()

		# guard against already currently being processed payloads via another entrypoint
		if psl.is_locked:
			psl.lock(timeout=5)  # allow ample 5 seconds to finish
			psl.reload()
		else:
			psl.lock()

		self.state = psl.load_state()
		self.state.response = response

		ref_doc = frappe.get_doc(
			self.state.tx_data.reference_doctype,
			self.state.tx_data.reference_docname,
		)

		mute = self._is_server_to_server()
		try:
			processed = self._process_response(psl, response, ref_doc)
			if self.flags.status_changed_to in self.flowstates.declined:
				try:
					msg = self._render_failure_message()
					ref_doc.flags.payment_failure_message = msg
					ref_doc.run_method("on_payment_failed", msg)
				except Exception:
					psl.log_error("Setting failure message on ref doc failed")

		except PayloadIntegrityError:
			error = psl.log_error("Response validation failure")
			if not mute:
				frappe.redirect_to_message(
					_("Server Error"),
					_("There's been an issue with your payment."),
					http_status_code=500,
					indicator_color="red",
				)
				raise frappe.Redirect

		except PaymentControllerProcessingError as e:
			error = psl.log_error(f"Processing error ({e.psltype})")
			psl.set_processing_payload(response, "Error")
			if not mute:
				frappe.redirect_to_message(
					_("Server Error"),
					_error_value(error, e.psltype),
					http_status_code=500,
					indicator_color="red",
				)
				raise frappe.Redirect

		except RefDocHookProcessingError as e:
			error = psl.log_error(f"Processing failure ({e.psltype} - refdoc hook)", e.__cause__)
			psl.set_processing_payload(response, "Error - RefDoc")
			if not mute:
				frappe.redirect_to_message(
					_("Server Error"),
					_error_value(error, f"{e.psltype} (via ref doc hook)"),
					http_status_code=500,
					indicator_color="red",
				)
				raise frappe.Redirect
		else:
			return processed
		finally:
			psl.unlock()

	# Lifecycle hooks (contracts)
	#  - imeplement them for your controller
	# ---------------------------------------

	def validate_tx_data(self, tx_data: TxData) -> None:
		"""Invoked by the reference document for example in order to validate the transaction data.

		Should throw on error with an informative user facing message.
		"""
		raise NotImplementedError

	def is_user_flow_initiation_delegated(self, psl_name: PSLName) -> bool:
		"""If true, you should initiate the user flow from the Ref Doc.

		For example, by sending an email (with a payment url), letting the user make a phone call or initiating a factoring process.

		If false, the gateway initiates the user flow.
		"""
		return False

	# Concrete controller methods
	#  - imeplement them for your gateway
	# ---------------------------------------

	def _patch_tx_data(self, tx_data: TxData) -> TxData:
		"""Optional: Implement tx_data preprocessing if required by the gateway.
		For example in order to fix rounding or decimal accuracy.
		"""
		return tx_data

	def _pre_data_capture_hook(self) -> dict:
		"""Optional: Implement additional server side control flow prior to data capture.
		For example in order to fetch additional data from the gateway that must be already present
		during the data capture.

		This is NOT used in Buttons with the Third Party Widget implementation variant.
		"""
		return {}

	def _should_have_mandate(self) -> bool:
		"""Optional: Define here, if the TxData store in self.state.tx_data should have a mandate.

		If yes, and the controller hasn't yet found one from a call to self._get_mandate(),
		it will initiate the adquisition of a new mandate in self._create_mandate().

		You have read (!) access to:
		- self.state.psl
		- self.state.tx_data
		"""
		assert self.state.psl
		assert self.state.tx_data
		return False

	def _get_mandate(self) -> PaymentMandate:
		"""Optional: Define here, how to fetch this controller's mandate doctype instance.

		Since a mandate might be highly controller specific, this is its accessor.

		You have read (!) access to:
		- self.state.psl
		- self.state.tx_data
		"""
		assert self.state.psl
		assert self.state.tx_data
		return None

	def _create_mandate(self) -> PaymentMandate:
		"""Optional: Define here, how to create controller's mandate doctype instance.

		Since a mandate might be highly controller specific, this is its constructor.

		You have read (!) access to:
		- self.state.psl
		- self.state.tx_data
		"""
		assert self.state.psl
		assert self.state.tx_data
		_help_me_develop(self.state)
		return None

	def _initiate_mandate_acquisition(self) -> Initiated:
		"""Invoked by proceed to initiate a mandate acquisiton flow.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _initiate_mandated_charge(self) -> Initiated:
		"""Invoked by proceed or after having aquired a mandate in order to initiate a mandated charge flow.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _initiate_charge(self) -> Initiated:
		"""Invoked by proceed in order to initiate a charge flow.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _validate_response(self) -> None:
		"""Implement how the validation of the response signature

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_mandate_acquisition(self) -> Processed | None:
		"""Implement how the controller should process mandate acquisition responses

		Needs to be idenmpotent.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_mandated_charge(self) -> Processed | None:
		"""Implement how the controller should process mandated charge responses

		Needs to be idenmpotent.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response

		Implementations can read/write:
		- self.state.mandate
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _process_response_for_charge(self) -> Processed | None:
		"""Implement how the controller should process charge responses

		Needs to be idenmpotent.

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _render_failure_message(self) -> str:
		"""Extract a readable failure message out of the server response

		Implementations can read:
		- self.state.psl
		- self.state.tx_data
		- self.state.response
		- self.state.mandate; if mandate is involved
		"""
		_help_me_develop(self.state)
		raise NotImplementedError

	def _is_server_to_server(self) -> bool:
		"""If this is a server to server processing flow.

		In this case, no errors will be returned.

		Implementations can read:
		- self.state.response
		"""
		_help_me_develop(self.state)
		raise NotImplementedError


@frappe.whitelist()
def frontend_defaults(doctype):
	c: PaymentController = get_controller(doctype)
	if issubclass(c, PaymentController):
		d: FrontendDefaults = c.frontend_defaults
		return d.__dict__
