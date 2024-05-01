import uuid
import json
import base64
import hashlib
import requests

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import (
    call_hook_method,
    cint,
    cstr,
    flt,
    get_decrypted_password,
    get_request_site_address,
    get_url,
)

from payments.utils import create_payment_gateway


def get_phonepe_config():
    """Returns PhonePe config"""
    phonepe_config = frappe.db.get_singles_dict("Phonepe Settings")
    phonepe_config.update(
        dict(
            merchantId=frappe.get_value("Phonepe Settings", "merchant_id"),
            saltKey=frappe.get_value("Phonepe Settings", "salt_key"),
            saltIndex=frappe.get_value("Phonepe Settings", "salt_index"),
			apiEndpoint=frappe.get_value("PhonePe Settings",api_endpoint),
        )
    )

    if cint(phonepe_config.get("sandbox")):
        phonepe_config.update(
            dict(
                url="https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/pay",
                transaction_status_url="https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/status/",
            )
        )
    else:
        phonepe_config.update(
            dict(
                url="https://api.phonepe.com/apis/hermes/pg/v1/pay",
                transaction_status_url="https://api.phonepe.com/apis/pg/v1/status/",
            )
        )
    return phonepe_config
	# Example usage
phonepe_config = get_phonepe_config()
print("PhonePe Merchant ID:", phonepe_config["merchantId"])

#-----------generate_phonepe_unique_txn_id--------------------------
def generate_phonepe_unique_id():
    # Generate a UUID (Universally Unique Identifier)
    unique_id = str(uuid.uuid4())

    # Remove hyphens and limit the length to 10 characters
    alphanumeric_id = unique_id.replace("-", "")[:10]

    return alphanumeric_id
phonepe_txn_id = generate_phonepe_unique_id()

#------------------------fetch_payment_details----------------------------
def fetch_payment_details():
    # Fetch payment details from system (e.g., order ID, amount, etc.)
    payload = {
		"merchantId": phonepe_config.merchant_id,
        "merchantTransactionId": phonepe_txn_id,
        "merchantUserId": payment_details["payer_mobileNumber"],
        "amount": cstr(flt(payment_details["amount"], 2))*100, # Amount in paisa (e.g., 1000 paisa = ₹10)\
        "redirectUrl": redirect_uri,
        "redirectMode": "REDIRECT",
        "callbackUrl": redirect_uri,
        "mobileNumber": payment_details["payer_mobileNumber"],
        "paymentInstrument": {"type": "PAY_PAGE"},
    }
    return payload

# Example usage
payload = fetch_payment_details()
print("Order ID:", payload["order_id"])

#----------------------base64 encoded payload------------------------
def encode_to_base64(payload):
    # Serialize payload to JSON
    json_payload = json.dumps(payload)

    # Encode JSON payload to Base64
    base64_payload = base64.b64encode(json_payload.encode()).decode()

    return base64_payload
#-----------------X-VERIFY------------------------------------------------
def calculate_x_verify(payload, salt_key, salt_index):
    # Get the Base64-encoded payload
    base64_payload = encode_to_base64(payload)  

    # Construct X-VERIFY string
    x_verify_string = (base64_payload + "/pg/v1/pay" + salt_key) + "###" + str(salt_index)

    # Calculate SHA256 hash
    sha256_hash = hashlib.sha256(x_verify_string.encode()).hexdigest()

    return sha256_hash

# ----------------Example usage----------------------------------------
phonepe_config = get_phonepe_config()
payload = fetch_payment_details()
sha256_hash = calculate_x_verify(payload, phonepe_config["saltKey"], phonepe_config["saltIndex"])

print("X-VERIFY final SHA256:", sha256_hash)
#------------------------------------------------------------------------

def initiate_phonepe_payment(phonepe_txn_id, amount):
    url = phonepe_config["url"]
    base64_payload = encode_to_base64(payload)  # Get the Base64-encoded payload

    headers = {
        "accept": "text/plain",
        "Content-Type": "application/json",
        "X-VERIFY": sha256_hash,
    }

    response = requests.post(url, json=base64_payload, headers=headers)
    print(response.text)

def verify_transaction_status(merchantId, phonepe_txn_id):
    url = f"https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/status/{phonepe_config['merchantId']}/{phonepe_txn_id}"

    headers = {
        "accept": "text/plain",
        "Content-Type": "application/json",
        "X-VERIFY": sha256_hash,
        "X-MERCHANT-ID": phonepe_config["merchantId"],
    }
    
def initiate_phonepe_refund(x_verify, base64_payload):
    url = "https://api-preprod.phonepe.com/apis/pg-sandbox/pg/v1/refund"

    payload = {"request": base64_payload}
    headers = {
        "accept": "text/plain",
        "Content-type": "application/json",
        "X-VERIFY": sha256_hash,
    }

    response = requests.post(url, json=payload, headers=headers)

    print("Refund Response:", response.text)
