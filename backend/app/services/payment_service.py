from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import razorpay
from fastapi import HTTPException
from sqlalchemy.orm import Session
import logging

from backend.app.models.user import User
from backend.app.models.shows import Show
from backend.app.models.movies import Movie
from backend.app.models.screens import Screen
from backend.app.models.theatre import Theatre
from backend.app.models.seat import Seat
from backend.app.services.booking_email import send_booking_confirmation

from backend.app.core.config import settings
from backend.app.models.payment import Payment, Status
from backend.app.models.booking import Booking
from backend.app.models.seat import SeatLock
from backend.app.models.booking_seat import BookingSeat


logger = logging.getLogger(__name__)

razorpay_client = razorpay.Client(
    auth=(
        settings.RAZORPAY_KEY_ID,
        settings.RAZORPAY_KEY_SECRET,
    )
)


def create_payment_order(
    db: Session,
    booking,
):
    """
    Create a Razorpay order for an existing booking.

    The caller must verify that the booking belongs
    to the authenticated user and is eligible for payment.
    """

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "Razorpay is not configured. Set RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET in the backend .env file."
            ),
        )

    amount = Decimal(str(booking.total_amount))

    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid booking amount",
        )

    # Razorpay expects INR in paise.
    amount_paise = int(
        (amount * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )

    try:
        order = razorpay_client.order.create(
            data={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": f"booking_{booking.booking_id}",
            }
        )
    except Exception as exc:
        logger.exception("Razorpay order creation failed")
        raise HTTPException(
            status_code=502,
            detail="Unable to create Razorpay order",
        ) from exc

    payment = Payment(
        booking_id=booking.booking_id,
        final_amount=amount,
        payment_type="RAZORPAY",
        payment_status=Status.PENDING.value,
        razorpay_order_id=order["id"],
    )

    try:
        db.add(payment)
        db.commit()
        db.refresh(payment)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to save payment record",
        )

    return {
        "payment_id": payment.payment_id,
        "booking_id": payment.booking_id,
        "razorpay_order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "payment_status": payment.payment_status,
    }


def verify_payment(
    db: Session,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
):
    """
    Verify Razorpay Checkout response and update payment.
    """

    payment = (
        db.query(Payment)
        .filter(
            Payment.razorpay_order_id == razorpay_order_id
        )
        .with_for_update()
        .first()
    )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment order not found",
        )

    # Verify against the order ID already stored in our DB.
    try:
        razorpay_client.utility.verify_payment_signature(
            {
                "razorpay_order_id": payment.razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
    except razorpay.errors.SignatureVerificationError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid payment signature",
        ) from exc

    # Fetch payment details from Razorpay to confirm status
    # and ensure the payment matches the stored order.
    try:
        gateway_payment = razorpay_client.payment.fetch(
            razorpay_payment_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to verify payment status with Razorpay",
        ) from exc

    if gateway_payment.get("order_id") != payment.razorpay_order_id:
        raise HTTPException(
            status_code=400,
            detail="Payment does not match the order",
        )

    expected_amount = int(
        (Decimal(str(payment.final_amount)) * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )

    if gateway_payment.get("amount") != expected_amount:
        raise HTTPException(
            status_code=400,
            detail="Payment amount mismatch",
        )

    if gateway_payment.get("currency") != "INR":
        raise HTTPException(
            status_code=400,
            detail="Payment currency mismatch",
        )

    gateway_status = gateway_payment.get("status")

    if gateway_status != "captured":
        # A valid signature alone does not mean the
        # payment has been captured.
        raise HTTPException(
            status_code=409,
            detail=f"Payment is not captured: {gateway_status}",
        )

    # Make repeated successful verification requests safe.
    if payment.payment_status == Status.SUCCESS.value:
        if payment.razorpay_payment_id != razorpay_payment_id:
            raise HTTPException(
                status_code=409,
                detail="Payment already completed with another transaction",
            )

        return {
            "message": "Payment already verified",
            "payment_id": payment.payment_id,
            "payment_status": payment.payment_status,
        }

    if payment.payment_status != Status.PENDING.value:
        raise HTTPException(
            status_code=409,
            detail="Payment is not in a payable state",
        )

    payment.razorpay_payment_id = razorpay_payment_id
    payment.payment_status = Status.SUCCESS.value
    payment.paid_at = datetime.now()
    booking = db.get(Booking, payment.booking_id)
    if booking is not None:
        booking.status = "PAID"
        active_locks = (
            db.query(SeatLock)
            .filter(
                SeatLock.show_id == booking.show_id,
                SeatLock.user_id == booking.user_id,
                SeatLock.lock_status == "ACTIVE",
            )
            .all()
        )
        # Persist each purchased seat against this booking. This is done in
        # the same transaction as payment confirmation to avoid duplicate tickets.
        existing_booking_seats = {
            row.seat_id
            for row in db.query(BookingSeat).filter(
                BookingSeat.booking_id == booking.booking_id
            ).all()
        }
        seat_price_total = sum(
            Decimal("180.00") if lock.seat and lock.seat.seat_type.upper() == "PLATINUM"
            else Decimal("160.00")
            for lock in active_locks
        )
        remaining_amount = Decimal(str(payment.final_amount))
        for index, lock in enumerate(active_locks):
            lock.lock_status = "CONFIRMED"
            if lock.seat_id not in existing_booking_seats:
                price = (
                    Decimal("180.00")
                    if lock.seat and lock.seat.seat_type.upper() == "PLATINUM"
                    else Decimal("160.00")
                )
                db.add(BookingSeat(
                    booking_id=booking.booking_id,
                    seat_id=lock.seat_id,
                    show_id=booking.show_id,
                    price_paid=price,
                    is_active=True,
                ))

    try:
        db.commit()
        db.refresh(payment)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to update payment record",
        )

    # Email is sent only after the payment and confirmed seat locks are committed.
    # SMTP failure must never turn a successful payment into a failed booking.
    email_sent = False
    try:
        booking = db.get(Booking, payment.booking_id)
        if booking is not None:
            user = db.get(User, booking.user_id)
            show = db.get(Show, booking.show_id)
            movie = db.get(Movie, show.movie_id) if show else None
            screen = db.get(Screen, show.screen_id) if show else None
            theatre = db.get(Theatre, screen.theatre_id) if screen else None
            locks = (
                db.query(SeatLock)
                .filter(
                    SeatLock.show_id == booking.show_id,
                    SeatLock.user_id == booking.user_id,
                    SeatLock.lock_status == "CONFIRMED",
                )
                .all()
            )
            seat_labels = sorted(
                [lock.seat.seat_label for lock in locks if lock.seat is not None]
            )
            if user and user.user_email:
                send_booking_confirmation(
                    recipient=user.user_email,
                    booking_id=booking.booking_id,
                    movie_name=movie.movie_title if movie else "Movie",
                    theatre_name=theatre.name if theatre else "Theatre",
                    screen_name=screen.screen_name if screen else "Screen",
                    seat_labels=seat_labels,
                    amount=f"{payment.final_amount:.2f}",
                )
                email_sent = True
    except Exception:
        logger.exception("Payment succeeded, but booking confirmation email could not be sent")

    return {
        "message": "Payment verified successfully",
        "payment_id": payment.payment_id,
        "booking_id": payment.booking_id,
        "payment_status": payment.payment_status,
        "razorpay_payment_id": payment.razorpay_payment_id,
        "paid_at": payment.paid_at,
        "email_sent": email_sent,
    }