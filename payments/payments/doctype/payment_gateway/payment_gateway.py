# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE

from frappe.model.document import Document


class PaymentGateway(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		gateway: DF.Data
		gateway_controller: DF.DynamicLink | None
		gateway_settings: DF.Link | None
	# end: auto-generated types
	pass
