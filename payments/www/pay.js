frappe.ready(function() {

	// Focus the first button
	// document.getElementById("primary-button").focus();

	// Get all button elements
	const buttons = Array.from(document.getElementsByClassName('btn-pay'));

	// Get the error section
	// const errors = document.getElementById("errors");

	// Get the payment session log name
	const urlParams = new URLSearchParams(window.location.search);
	const pslName = urlParams.get('s');

	// Loop through each button and add the onclick event listener
	buttons.forEach((button) => {
    // Get the data-button attribute value
    const buttonData = button.getAttribute('data-button');

	  button.addEventListener('click', () => {

	    // Make the Frappe call
	    frappe.call({
	      method: "payments.payments.doctype.payment_session_log.payment_session_log.select_button",
	      args: {
					pslName: pslName,
					buttonName: buttonData,
				},
				error_msg: "#select-button-errors",
	      callback: (r) => {
					if (r.message.reload) {
						window.location.reload();
					}
	      }
	    });
	  });
	});
});

$(document).on("payment-submitted", function (e) {
	$("div#button-section").hide()
})

$(document).on("payment-processed", function (e, r) {
	if (r.message.status_changed_to) {
		const status = r.message.status_changed_to;
		const color = r.message.indicator_color;
		const pill = $("#status");
		pill.html(status);
		pill.addClass(color)
		$("#status-wrapper").toggle(true)
		const indicator = $("#refdoc-indicator");
		indicator.removeClass(function(_, className) {
			return className.match(/blue|red/g).join(" ");
		});
		indicator.addClass(color);
	}
	if (r.message.message) {
		$("#message").html(r.message.message).toggle(true)
		$("#message-wrapper").toggle(true)
	}
	if (r.message.action) {
		const cta = $("#action-processed");
		cta.html(r.message.action.label)
		cta.attr("href", r.message.action.href)
		cta.toggle(true)
		cta.focus()
		if (r.message.action.redirect_after_milliseconds) {
			const message = $("#action-redirect-message");
			const secondsCounter = $("#action-redirect-message-seconds");
			let seconds = Math.floor(r.message.action.redirect_after_milliseconds / 1000);
			secondsCounter.html(seconds);
      function updateCounter() {
          seconds--;
          secondsCounter.html(seconds);
      }
			message.toggle(true)
			setInterval(updateCounter, 1000);
			setTimeout(function() {
				window.location.href = r.message.action.href
			}, r.message.action.redirect_after_milliseconds);
		}
	}
})
