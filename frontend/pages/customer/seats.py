
"""Customer seat selection page."""

import streamlit as st

from common import api

MAX_SEATS = 10
GOLD_PRICE = 160
PLATINUM_PRICE = 180


def _seat_price(seat: dict) -> int:
    return (
        PLATINUM_PRICE
        if str(seat.get("seat_type", "GOLD")).upper() == "PLATINUM"
        else GOLD_PRICE
    )


def _render_seat_styles():
    """Style seat buttons and cinema screen using inline CSS."""

    st.markdown(
        """
        <style>
        /* Available seats */
        div[data-testid="stButton"] > button[kind="secondary"] {
            color: #16803c;
            background-color: #e8f5e9;
            border: 1px solid #16803c;
            border-radius: 10px;
            min-height: 48px;
            font-weight: 600;
        }

        div[data-testid="stButton"] > button[kind="secondary"]:hover {
            background-color: #c8e6c9;
            border-color: #15803d;
            color: #166534;
        }

        /* Selected seats */
        div[data-testid="stButton"] > button[kind="primary"] {
            color: #ffffff;
            background-color: #2563eb;
            border: 1px solid #1d4ed8;
            border-radius: 10px;
            min-height: 48px;
            font-weight: 600;
        }

        div[data-testid="stButton"] > button[kind="primary"]:hover {
            background-color: #1d4ed8;
            color: #ffffff;
        }

        /* Booked or held seats */
        div[data-testid="stButton"] > button:disabled {
            color: #dc2626;
            background-color: #fee2e2;
            border: 1px solid #dc2626;
            border-radius: 10px;
            min-height: 48px;
            font-weight: 600;
            opacity: 1;
            cursor: not-allowed;
        }

        /* Seat legend */
        .seat-legend {
            display: flex;
            justify-content: center;
            align-items: center;
            flex-wrap: wrap;
            gap: 18px;
            padding: 14px;
            margin: 15px 0 20px 0;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            font-size: 14px;
        }

        .seat-legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
            font-weight: 600;
        }

        .seat-legend-icon {
            font-size: 22px;
        }

        /* Cinema screen */
        .cinema-heading {
            text-align: center;
            color: #64748b;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 4px;
            padding: 20px 0 12px 0;
        }

        .cinema-screen-line {
            width: 85%;
            height: 7px;
            margin: 0 auto;
            border-radius: 50%;
            background: linear-gradient(
                90deg,
                transparent,
                #60a5fa,
                #2563eb,
                #60a5fa,
                transparent
            );
            box-shadow: 0 5px 18px rgba(37, 99, 235, 0.2);
        }

        .cinema-screen {
            width: 85%;
            box-sizing: border-box;
            margin: 8px auto 25px auto;
            padding: 13px;
            text-align: center;
            color: #1558a6;
            background-color: #e8edf5;
            border-radius: 8px;
            font-weight: 700;
            letter-spacing: 5px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.dialog("How many seats do you need?", width="small")
def choose_seat_count():
    # Inject CSS to hide the close button (the cross option)
    st.html(
        """
        <style>
        button[aria-label="Close"] {
            display: none !important;
        }
        </style>
        """
    )
    
    count = st.selectbox(
        "Number of seats",
        options=list(range(0, MAX_SEATS + 1)),
        index=1,
        key="booking_seat_count_choice",
    )
    st.caption(
        f"Gold: ₹{GOLD_PRICE} per seat · "
        f"Platinum: ₹{PLATINUM_PRICE} per seat"
    )
    if st.button(
        "Continue",
        type="primary",
        use_container_width=True,
    ):
        st.session_state["booking_requested_count"] = count
        st.session_state["booking_seat_ids"] = []
        st.session_state["booking_show_price_dialog"] = False
        st.rerun()



@st.dialog("Confirm your seats", width="small")
def confirm_seat_price(total: int, labels: list[str]):
    st.markdown(f"### Total price: ₹{total}")
    st.write("**Selected seats:** " + ", ".join(labels))
    st.caption("Your seats will be held for five minutes when you continue.")

    pay_col, back_col = st.columns(2)

    with pay_col:
        if st.button(
            "Continue Payment",
            type="primary",
            use_container_width=True,
        ):
            show_id = st.session_state["booking_show_id"]
            selected_ids = st.session_state.get(
                "booking_seat_ids", []
            )

            ok, result, status_code = api(
                "POST",
                "/seats/lock",
                json={
                    "show_id": show_id,
                    "seat_ids": selected_ids,
                },
            )

            if ok:
                st.session_state["booking_lock_show_id"] = show_id
                st.session_state["booking_lock_seats"] = result
                st.session_state["booking_step"] = "payment"
                st.session_state["booking_show_price_dialog"] = False
                st.rerun()

            elif status_code == 409:
                st.session_state["booking_show_price_dialog"] = False
                st.session_state["booking_seat_ids"] = []
                st.error(
                    "One or more seats are no longer available. "
                    "Please select again."
                )
                st.rerun()

            else:
                st.error(f"Unable to hold seats: {result}")

    with back_col:
        if st.button("Go Back", key="go_back_from_payment", use_container_width=True):
            # No backend lock exists yet, so just clear the selection.
            st.session_state["booking_lock_show_id"] = None
            st.session_state["booking_lock_seats"] = []
            st.session_state["booking_seat_ids"] = []
            st.session_state["booking_step"] = "seats"
            st.rerun()


def seats_page():
    show_id = st.session_state.get("booking_show_id")

    if show_id is None:
        st.warning("Please select a show timing first.")
        return

    
    if "booking_requested_count" not in st.session_state:
        choose_seat_count()
        return

    requested_count = st.session_state["booking_requested_count"]

    if requested_count == 0:
        st.info(
            "You selected zero seats. "
            "Choose a positive number to select seats."
        )

        if st.button("Choose seat count again"):
            st.session_state.pop("booking_requested_count", None)
            st.rerun()

        return

    ok, seats, status_code = api(
        "GET",
        f"/seats/show/{show_id}",
    )

    if not ok:
        st.error(f"Unable to load seats: {seats}")
        st.caption(f"HTTP status: {status_code}")
        return

    if not seats:
        st.info("No seats have been generated for this screen yet.")
        return

    _render_seat_styles()
     
    if st.button("← Go Back to Theaters & Shows", key="back_to_shows"):
        st.session_state.pop("booking_requested_count", None)
        st.session_state["booking_seat_ids"] = []
        st.session_state["booking_show_price_dialog"] = False
        st.session_state["booking_step"] = "theatres"
        st.rerun()
     
    st.title(
        st.session_state.get(
            "booking_movie_title",
            "Selected Movie",
        )
    )

    st.subheader(
        st.session_state.get(
            "booking_theatre_name",
            "Selected Theatre",
        )
    )

    seats_by_id = {
        seat["seat_id"]: seat
        for seat in seats
    }

    selected_ids = st.session_state.get(
        "booking_seat_ids", []
    )

    # Keep only seats that are still available.
    selected_ids = [
        seat_id
        for seat_id in selected_ids
        if seat_id in seats_by_id
        and str(
            seats_by_id[seat_id].get(
                "seat_status", "AVAILABLE"
            )
        ).upper() == "AVAILABLE"
    ]

    st.session_state["booking_seat_ids"] = selected_ids

    # Seat status legend.
    
    st.markdown(
    """
    <div style="display:flex;justify-content:center;align-items:center;gap:30px;flex-wrap:wrap;padding:16px;margin:15px 0 20px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;">
    <div style="display:flex;align-items:center;gap:9px;"><span style="background:#dcfce7;border:1px solid #16a34a;border-radius:8px;padding:7px 10px;font-size:22px;">💺</span><span style="color:#15803d;font-weight:600;">Available</span></div>
    <div style="display:flex;align-items:center;gap:9px;"><span style="background:#dbeafe;border:1px solid #2563eb;border-radius:8px;padding:7px 10px;font-size:22px;">💺</span><span style="color:#1d4ed8;font-weight:600;">Selected</span></div>
    <div style="display:flex;align-items:center;gap:9px;"><span style="background:#fee2e2;border:1px solid #dc2626;border-radius:8px;padding:7px 10px;font-size:22px;">💺</span><span style="color:#dc2626;font-weight:600;">Booked</span></div>
    </div>
    """,
    unsafe_allow_html=True,
    )
    
    
   # Change seat count button
    if st.button("🖍️ Seats ", key="change_seat_count"):
        st.session_state.pop("booking_requested_count", None)
        st.session_state["booking_seat_ids"] = []
        st.session_state["booking_show_price_dialog"] = False
        st.rerun()

# Display selection instruction directly below the button
    st.write(f"Select **{requested_count}** seat(s).")

    platinum_seats = [
        seat
        for seat in seats
        if str(
            seat.get("seat_type", "GOLD")
        ).upper() == "PLATINUM"
    ]

    gold_seats = [
        seat
        for seat in seats
        if str(
            seat.get("seat_type", "GOLD")
        ).upper() != "PLATINUM"
    ]

    def render_section(
        section_name: str,
        section_seats: list[dict],
    ):
        if not section_seats:
            return

        if section_name == "PLATINUM":
            st.markdown(f"### ₹{PLATINUM_PRICE} PLATINUM")
        else:
            st.markdown(f"### ₹{GOLD_PRICE} GOLD")

        row_numbers = sorted(
            {seat["row_no"] for seat in section_seats},
            reverse=True,
        )

        for row_no in row_numbers:
            row_seats = sorted(
                [
                    seat
                    for seat in section_seats
                    if seat["row_no"] == row_no
                ],
                key=lambda seat: seat["col_no"],
            )

            cols = st.columns(len(row_seats))

            for col, seat in zip(cols, row_seats):
                seat_id = seat["seat_id"]
                label = seat["seat_label"]

                status = str(
                    seat.get("seat_status", "AVAILABLE")
                ).upper()

                is_selected = (
                    seat_id
                    in st.session_state["booking_seat_ids"]
                )

                if status in ("BOOKED", "HOLD"):
                    button_label = f"💺 {label}"
                    disabled = True
                    button_type = "secondary"

                elif is_selected:
                    button_label = f"💺 {label} ✓"
                    disabled = False
                    button_type = "primary"

                else:
                    button_label = f"💺 {label}"
                    disabled = False
                    button_type = "secondary"

                with col:
                    if st.button(
                        button_label,
                        key=f"seat_{show_id}_{seat_id}",
                        disabled=disabled,
                        type=button_type,
                        use_container_width=True,
                    ):
                        current = list(
                            st.session_state["booking_seat_ids"]
                        )

                        if seat_id in current:
                            current.remove(seat_id)

                        elif len(current) < requested_count:
                            current.append(seat_id)

                        st.session_state["booking_seat_ids"] = current

                        if len(current) == requested_count:
                            st.session_state[
                                "booking_show_price_dialog"
                            ] = True
                        else:
                            st.session_state[
                                "booking_show_price_dialog"
                            ] = False

                        st.rerun()

        st.divider()

    render_section("PLATINUM", platinum_seats)
    render_section("GOLD", gold_seats)

    # Cinema screen.
    st.markdown(
        """
        <div class="cinema-heading">
            ALL EYES THIS WAY
        </div>

        <div class="cinema-screen-line"></div>

        <div class="cinema-screen">
            SCREEN
        </div>
        """,
        unsafe_allow_html=True,
    )

    selected_seats = [
        seats_by_id[seat_id]
        for seat_id in st.session_state["booking_seat_ids"]
        if seat_id in seats_by_id
    ]

    if (
        st.session_state.get("booking_show_price_dialog")
        and len(selected_seats) == requested_count
    ):
        total = sum(
            _seat_price(seat)
            for seat in selected_seats
        )

        labels = [
            seat["seat_label"]
            for seat in selected_seats
        ]

        confirm_seat_price(total, labels)