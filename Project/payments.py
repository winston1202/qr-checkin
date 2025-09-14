from flask import Blueprint, request, redirect, url_for, g, flash, render_template, current_app
from .models import db, Team
from .decorators import admin_required
import stripe
import os

payments_bp = Blueprint('payments', __name__)


@payments_bp.route("/create-checkout-session", methods=["POST"])
@admin_required
def create_checkout_session():
    """Creates a Stripe Checkout session to upgrade the user to Pro."""
    price_id = os.environ.get('STRIPE_PRICE_ID')
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=url_for('payments.success', _external=True),
            cancel_url=url_for('payments.cancel', _external=True),
            # Pass the team_id through so we know who subscribed in the webhook
            client_reference_id=g.user.team_id 
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Error communicating with Stripe: {e}", "error")
        return redirect(url_for('auth.pricing'))

@payments_bp.route("/success")
def success():
    flash("Payment successful! Your team has been upgraded to the Pro plan.", "success")
    return redirect(url_for('admin.dashboard'))

@payments_bp.route("/cancel")
def cancel():
    flash("Payment was cancelled. Your plan has not been changed.", "error")
    return redirect(url_for('auth.pricing'))

@payments_bp.route("/create-portal-session", methods=["POST"])
@admin_required
def create_portal_session():
    """Creates a Stripe Customer Portal session for the user to manage their subscription."""
    if not g.user.team.stripe_customer_id:
        flash("No subscription found to manage.", "error")
        return redirect(url_for('admin.dashboard'))
    
    portal_session = stripe.billing_portal.Session.create(
        customer=g.user.team.stripe_customer_id,
        return_url=url_for('admin.dashboard', _external=True),
    )
    return redirect(portal_session.url, code=303)

@payments_bp.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    """
    Listens for events from Stripe to update the database reliably.
    This is critical for production.
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        return 'Invalid payload or signature', 400

    # Handle the checkout.session.completed event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        team_id = session.get('client_reference_id')
        customer_id = session.get('customer')
        
        team = Team.query.get(team_id)
        if team:
            team.plan = 'Pro'
            team.stripe_customer_id = customer_id
            db.session.commit()

    # Handle other events like subscription cancellations
    if event['type'] == 'customer.subscription.deleted':
        session = event['data']['object']
        customer_id = session.get('customer')
        
        team = Team.query.filter_by(stripe_customer_id=customer_id).first()
        if team:
            team.plan = 'Free'
            db.session.commit()

    return 'OK', 200