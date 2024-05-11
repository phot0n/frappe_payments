import frappe
import grpc
from payments.payment_gateways.doctype.custom_payment_settings.databank import users_pb2, users_pb2_grpc, payments_pb2, payments_pb2_grpc
from payments.payment_gateways.doctype.custom_payment_settings.custom_payment_settings import (
	get_gateway_controller,
)



@frappe.whitelist(allow_guest=True)
def create_account(username, password):
	gateway_controller='duri wallet'
	channel=frappe.get_doc("Custom Payment Settings", gateway_controller).configure_wallet()
	domain_url=frappe.get_doc("Custom Payment Settings", gateway_controller).configure_domain()
	details= users_pb2.request(username=frappe.session.user, domain=domain_url)
	try:
		details= users_pb2.request(username=username, domain=domain_url, password=password)
		stub = users_pb2_grpc.userServiceStub(channel)
		response = stub.CreateAccount(details)
		if response.info.information=='200 OK':
			return response.info.information
		else:
			frappe.throw(response.error.localizedDescription)
	except grpc.RpcError as e:
			frappe.throw(f"{e.code()}")