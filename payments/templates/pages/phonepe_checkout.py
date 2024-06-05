# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import json

import frappe
from frappe import _

from  payments.payment_gateways.doctype.phonepe_settings.phonepe_settings import(
        initiate_payment
)

no_cache = 1

def get_context(context):
    context.no_cache = 1
    payment_url = initiate_payment()
    return {
        "payment_url": payment_url
    }
