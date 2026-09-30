#!/usr/bin/env python3
"""
seed_clinical_ecosystem.py
==========================
End-to-End Clinical Data Seeding Suite for OpenMRS 3 and Odoo 19.

This script sets up a cohesive patient journey:
1. Configures 5 hospital products in Odoo 19 with internal references, pricing,
   and seeds initial stock with lot tracking and expiry dates.
2. Registers 3 realistic mock patients in OpenMRS via FHIR R4 API using valid
   Luhn Mod-30 check-digit OpenMRS IDs generated via OpenMRS Idgen.
3. Simulates clinical journeys for the 3 patients:
   - Patient A: Routine Outpatient Consultation + Paracetamol 500mg prescription.
   - Patient B: Emergency Visit + Blood Test (FBC) Lab Order + Amoxicillin 250mg prescription.
   - Patient C: Pediatric Wellness Consultation + Consultation billing only.
4. Outputs a structured summary table detailing Patient UUIDs, Encounter IDs,
   Order IDs, and expected billable totals.
"""

import sys
import os
import argparse
import datetime
import json
import requests
from typing import Dict, Any, List, Optional

# Defaults
DEFAULT_OPENMRS_URL = os.environ.get("OPENMRS_URL", "http://localhost:8080")
DEFAULT_OPENMRS_USER = os.environ.get("OPENMRS_USER", "admin")
DEFAULT_OPENMRS_PASS = os.environ.get("OPENMRS_PASS", "Admin123")

DEFAULT_ODOO_URL = os.environ.get("ODOO_URL", "http://localhost:8069")
DEFAULT_ODOO_DB = os.environ.get("ODOO_DB", "clinic_db")
DEFAULT_ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "1f4624cfdc7ce0fea669677b8ff485ad087f720c")


class OdooClient:
    """Client for Odoo 19 External JSON-2 API."""

    def __init__(self, base_url: str, db: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.db = db
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"bearer {self.api_key}",
            "X-Odoo-Database": self.db,
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def search_read(self, model: str, domain: list, fields: list, limit: int = 0) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/json/2/{model}/search_read"
        payload = {"domain": domain, "fields": fields}
        if limit > 0:
            payload["limit"] = limit
        resp = self.session.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def create(self, model: str, vals: Dict[str, Any]) -> int:
        url = f"{self.base_url}/json/2/{model}/create"
        resp = self.session.post(url, json={"vals_list": [vals]}, timeout=30)
        if not resp.ok:
            raise RuntimeError(f"Odoo create {model} failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        return data

    def write(self, model: str, ids: List[int], vals: Dict[str, Any]) -> bool:
        url = f"{self.base_url}/json/2/{model}/write"
        resp = self.session.post(url, json={"ids": ids, "vals": vals}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def call_button(self, model: str, method: str, ids: List[int]) -> Any:
        url = f"{self.base_url}/json/2/{model}/{method}"
        resp = self.session.post(url, json={"ids": ids}, timeout=30)
        resp.raise_for_status()
        return resp.json()


class OpenMRSClient:
    """Client for OpenMRS 3 FHIR R4 and REST APIs."""

    def __init__(self, base_url: str, user: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.fhir_url = f"{self.base_url}/openmrs/ws/fhir2/R4"
        self.rest_url = f"{self.base_url}/openmrs/ws/rest/v1"
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({"Accept": "application/json"})

    def generate_openmrs_id(self) -> str:
        """Fetch a valid Luhn Mod-30 check-digit OpenMRS ID from idgen."""
        url = f"{self.rest_url}/idgen/identifiersource/8549f706-7e85-4c1d-9424-217d50a2988b/identifier"
        resp = self.session.post(url, json={}, headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json().get("identifier")
        raise RuntimeError(f"Failed to generate OpenMRS ID: {resp.status_code} {resp.text}")

    def create_patient_fhir(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register patient via FHIR R4."""
        url = f"{self.fhir_url}/Patient"
        resp = self.session.post(
            url,
            json=patient_data,
            headers={"Content-Type": "application/fhir+json"},
            timeout=30
        )
        if resp.status_code in (200, 201):
            return resp.json()
        raise RuntimeError(f"Failed to create patient: {resp.status_code} {resp.text}")

    def create_encounter_rest(self, encounter_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create clinical encounter via REST API."""
        url = f"{self.rest_url}/encounter"
        resp = self.session.post(
            url,
            json=encounter_data,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if resp.status_code in (200, 201):
            return resp.json()
        raise RuntimeError(f"Failed to create encounter: {resp.status_code} {resp.text}")

    def create_order_rest(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create drug or test order via REST API."""
        url = f"{self.rest_url}/order"
        resp = self.session.post(
            url,
            json=order_data,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if resp.status_code in (200, 201):
            return resp.json()
        raise RuntimeError(f"Failed to create order: {resp.status_code} {resp.text}")


# Product definition catalog
PRODUCTS_CONFIG = [
    {
        "name": "General Consultation Fee",
        "default_code": "CONS-GEN",
        "type": "service",
        "is_storable": False,
        "tracking": "none",
        "list_price": 50.00,
        "concept_uuid": "167410AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "concept_display": "Clinical consultation",
        "stock_qty": 0,
    },
    {
        "name": "Full Blood Count (FBC)",
        "default_code": "LAB-FBC",
        "type": "service",
        "is_storable": False,
        "tracking": "none",
        "list_price": 35.00,
        "concept_uuid": "1019AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "concept_display": "Full blood count",
        "stock_qty": 0,
    },
    {
        "name": "Paracetamol 500mg Tabs",
        "default_code": "DRUG-PARA500",
        "type": "consu",
        "is_storable": True,
        "tracking": "lot",
        "list_price": 5.00,
        "concept_uuid": "70116AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "drug_uuid": "fbf74fd6-b37c-4325-86cb-dcaf4aabed81",
        "concept_display": "Paracetamol 500mg",
        "stock_qty": 200,
        "lot_name": "LOT-PARA-2026-001",
        "expiration_date": "2027-12-31 23:59:59",
    },
    {
        "name": "Amoxicillin 250mg Suspension",
        "default_code": "DRUG-AMOX250",
        "type": "consu",
        "is_storable": True,
        "tracking": "lot",
        "list_price": 12.00,
        "concept_uuid": "71160AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "drug_uuid": "0e99b832-3cad-4039-bfb5-0c2f55906f8a",
        "concept_display": "Amoxicillin 250mg/5ml",
        "stock_qty": 100,
        "lot_name": "LOT-AMOX-2026-001",
        "expiration_date": "2027-06-30 23:59:59",
    },
    {
        "name": "X-Ray Chest",
        "default_code": "RAD-XRAY-CHEST",
        "type": "service",
        "is_storable": False,
        "tracking": "none",
        "list_price": 75.00,
        "concept_uuid": "12AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "concept_display": "X-ray chest",
        "stock_qty": 0,
    },
    {
        "name": "Inpatient Ward Bed & Nursing Care",
        "default_code": "INP-BED",
        "type": "service",
        "is_storable": False,
        "tracking": "none",
        "list_price": 100.00,
        "concept_uuid": "e8d0e70a-4a25-4c07-b24f-ef7700e70487",
        "concept_display": "Inpatient bed stay",
        "stock_qty": 0,
    }
]


def setup_odoo_products_and_inventory(odoo: OdooClient) -> Dict[str, Dict[str, Any]]:
    """Create or update 5 standard hospital products and seed medication inventory."""
    print("\n[+] Setting up Odoo 19 Hospital Products & Stock...")
    
    # 1. Resolve WH/Stock location
    locations = odoo.search_read("stock.location", [("complete_name", "=", "WH/Stock")], ["id", "name"], limit=1)
    if not locations:
        raise RuntimeError("WH/Stock location not found in Odoo!")
    stock_loc_id = locations[0]["id"]
    print(f"    * Resolved Inventory Stock Location: WH/Stock (ID: {stock_loc_id})")

    product_map = {}

    for p in PRODUCTS_CONFIG:
        code = p["default_code"]
        existing = odoo.search_read("product.product", [("default_code", "=", code)], ["id", "name", "qty_available"], limit=1)
        
        if existing:
            prod_id = existing[0]["id"]
            print(f"    * Product '{p['name']}' ({code}) already exists (ID: {prod_id})")
        else:
            tmpl_vals = {
                "name": p["name"],
                "default_code": code,
                "type": p["type"],
                "is_storable": p["is_storable"],
                "tracking": p["tracking"],
                "list_price": p["list_price"],
                "description_sale": f"Concept: {p['concept_uuid']}"
            }
            tmpl_id = odoo.create("product.template", tmpl_vals)
            prods = odoo.search_read("product.product", [("product_tmpl_id", "=", tmpl_id)], ["id", "name"], limit=1)
            prod_id = prods[0]["id"]
            print(f"    * Created Product '{p['name']}' ({code}) -> ID: {prod_id}")

        # Seed inventory if product is storable medication
        if p["is_storable"] and p["stock_qty"] > 0:
            lot_name = p["lot_name"]
            lots = odoo.search_read("stock.lot", [("name", "=", lot_name), ("product_id", "=", prod_id)], ["id"], limit=1)
            if lots:
                lot_id = lots[0]["id"]
            else:
                lot_vals = {
                    "name": lot_name,
                    "product_id": prod_id
                }
                if p.get("expiration_date"):
                    lot_vals["expiration_date"] = p["expiration_date"]
                try:
                    lot_id = odoo.create("stock.lot", lot_vals)
                except Exception as e:
                    # Fallback without expiration_date if product_expiry is not active
                    if "expiration_date" in lot_vals:
                        del lot_vals["expiration_date"]
                        lot_id = odoo.create("stock.lot", lot_vals)
                    else:
                        raise e
                print(f"      - Created Lot: {lot_name} (ID: {lot_id})")

            # Check stock quant
            quants = odoo.search_read(
                "stock.quant",
                [("product_id", "=", prod_id), ("location_id", "=", stock_loc_id), ("lot_id", "=", lot_id)],
                ["id", "quantity"],
                limit=1
            )
            curr_qty = quants[0]["quantity"] if quants else 0.0
            if curr_qty < p["stock_qty"]:
                needed = p["stock_qty"] - curr_qty
                q_id = odoo.create("stock.quant", {
                    "product_id": prod_id,
                    "location_id": stock_loc_id,
                    "lot_id": lot_id,
                    "inventory_quantity": p["stock_qty"]
                })
                odoo.call_button("stock.quant", "action_apply_inventory", [q_id])
                print(f"      - Seeded stock inventory: {p['stock_qty']} units (applied adjustment)")
            else:
                print(f"      - Existing stock sufficient: {curr_qty} units")

        p_info = dict(p)
        p_info["odoo_id"] = prod_id
        product_map[code] = p_info

    return product_map


def seed_patients_and_journeys(openmrs: OpenMRSClient, product_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Register 3 mock patients in OpenMRS and simulate clinical encounters & orders."""
    print("\n[+] Registering Mock Patients & Simulating Clinical Journeys in OpenMRS...")

    location_uuid = "44c3efb0-2583-4c80-a79e-1f7560f7c0a1"  # Site 42 / Consultation clinic
    provider_uuid = "705f5791-07a7-44b8-932f-a81f3526fc98"  # Jake Doctor
    encounter_role_uuid = "240b26f9-dd88-4172-823d-4a8bfeb7841f"
    consultation_type_uuid = "dd528487-82a5-4082-9c72-ed246bd49591"
    outpatient_care_uuid = "6f0c9a92-6f24-11e3-af88-005056821db0"

    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=15)
    encounter_time_str = now.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    mock_patients_def = [
        {
            "given": "James",
            "family": "Mwangi",
            "gender": "male",
            "birthDate": "1988-04-14",
            "phone": "+254711223344",
            "line": "142 Hospital Road",
            "city": "Nairobi",
            "state": "Nairobi County",
            "postalCode": "00100",
            "country": "Kenya",
            "journey": "Patient A: Outpatient Routine Consultation + Paracetamol Prescription",
            "orders": [
                {
                    "type": "drugorder",
                    "concept": product_map["DRUG-PARA500"]["concept_uuid"],
                    "drug": product_map["DRUG-PARA500"]["drug_uuid"],
                    "name": product_map["DRUG-PARA500"]["name"],
                    "dose": 500,
                    "doseUnits": "161553AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  # mg
                    "route": "160240AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",      # Oral
                    "frequency": "136ebdb7-e989-47cf-8ec6-4e8b2ffe0ab5",  # TID (3x daily)
                    "quantity": 20,
                    "quantityUnits": "1513AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", # Tablet
                    "price": product_map["DRUG-PARA500"]["list_price"]
                }
            ],
            "consultation_fee": product_map["CONS-GEN"]["list_price"]
        },
        {
            "given": "Amina",
            "family": "Hassan",
            "gender": "female",
            "birthDate": "1995-11-20",
            "phone": "+254722334455",
            "line": "88 Uhuru Highway",
            "city": "Nairobi",
            "state": "Nairobi County",
            "postalCode": "00200",
            "country": "Kenya",
            "journey": "Patient B: Emergency Visit + Full Blood Count Lab + Amoxicillin Prescription",
            "orders": [
                {
                    "type": "testorder",
                    "concept": product_map["LAB-FBC"]["concept_uuid"],
                    "name": product_map["LAB-FBC"]["name"],
                    "price": product_map["LAB-FBC"]["list_price"]
                },
                {
                    "type": "drugorder",
                    "concept": product_map["DRUG-AMOX250"]["concept_uuid"],
                    "drug": product_map["DRUG-AMOX250"]["drug_uuid"],
                    "name": product_map["DRUG-AMOX250"]["name"],
                    "dose": 250,
                    "doseUnits": "161553AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  # mg
                    "route": "160240AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",      # Oral
                    "frequency": "136ebdb7-e989-47cf-8ec6-4e8b2ffe0ab5",  # TID
                    "quantity": 1,
                    "quantityUnits": "162353AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", # Bottle
                    "price": product_map["DRUG-AMOX250"]["list_price"]
                }
            ],
            "consultation_fee": product_map["CONS-GEN"]["list_price"]
        },
        {
            "given": "Liam",
            "family": "Kiprono",
            "gender": "male",
            "birthDate": "2022-08-05",
            "phone": "+254733445566",
            "line": "12 Acacia Avenue",
            "city": "Nairobi",
            "state": "Nairobi County",
            "postalCode": "00501",
            "country": "Kenya",
            "journey": "Patient C: Pediatric Wellness Visit + Consultation Only",
            "orders": [],
            "consultation_fee": product_map["CONS-GEN"]["list_price"]
        }
    ]

    results = []

    for p_def in mock_patients_def:
        # Step 1: Generate valid OpenMRS ID with Luhn Mod-30 check digit
        omrs_id = openmrs.generate_openmrs_id()

        # Step 2: Register Patient via FHIR R4
        fhir_patient_payload = {
            "resourceType": "Patient",
            "active": True,
            "name": [{
                "use": "official",
                "family": p_def["family"],
                "given": [p_def["given"]]
            }],
            "gender": p_def["gender"],
            "birthDate": p_def["birthDate"],
            "telecom": [{
                "system": "phone",
                "value": p_def["phone"],
                "use": "mobile"
            }],
            "address": [{
                "use": "home",
                "line": [p_def["line"]],
                "city": p_def["city"],
                "state": p_def["state"],
                "postalCode": p_def["postalCode"],
                "country": p_def["country"]
            }],
            "identifier": [{
                "use": "official",
                "type": {
                    "coding": [{"code": "05a29f94-c0ed-11e2-94be-8c13b969e334"}],
                    "text": "OpenMRS ID"
                },
                "value": omrs_id,
                "extension": [{
                    "url": "http://fhir.openmrs.org/ext/patient/identifier#location",
                    "valueReference": {
                        "reference": f"Location/{location_uuid}",
                        "type": "Location"
                    }
                }]
            }]
        }

        patient_res = openmrs.create_patient_fhir(fhir_patient_payload)
        patient_uuid = patient_res["id"]
        full_name = f"{p_def['given']} {p_def['family']}"
        print(f"    * Patient Registered: {full_name} | ID: {omrs_id} | UUID: {patient_uuid}")

        # Step 3: Create Encounter (Consultation)
        enc_payload = {
            "patient": patient_uuid,
            "encounterType": consultation_type_uuid,
            "encounterDatetime": encounter_time_str,
            "location": location_uuid,
            "encounterProviders": [{
                "provider": provider_uuid,
                "encounterRole": encounter_role_uuid
            }]
        }
        enc_res = openmrs.create_encounter_rest(enc_payload)
        encounter_uuid = enc_res["uuid"]
        print(f"      - Encounter Created: {encounter_uuid} ({p_def['journey']})")

        # Step 4: Create Orders
        created_orders = []
        orders_total = 0.0

        for ord_def in p_def["orders"]:
            if ord_def["type"] == "drugorder":
                ord_payload = {
                    "type": "drugorder",
                    "patient": patient_uuid,
                    "concept": ord_def["concept"],
                    "drug": ord_def["drug"],
                    "encounter": encounter_uuid,
                    "orderer": provider_uuid,
                    "careSetting": outpatient_care_uuid,
                    "dosingType": "org.openmrs.SimpleDosingInstructions",
                    "dose": ord_def["dose"],
                    "doseUnits": ord_def["doseUnits"],
                    "route": ord_def["route"],
                    "frequency": ord_def["frequency"],
                    "quantity": ord_def["quantity"],
                    "quantityUnits": ord_def["quantityUnits"],
                    "numRefills": 0,
                    "action": "NEW"
                }
            elif ord_def["type"] == "testorder":
                ord_payload = {
                    "type": "testorder",
                    "patient": patient_uuid,
                    "concept": ord_def["concept"],
                    "encounter": encounter_uuid,
                    "orderer": provider_uuid,
                    "careSetting": outpatient_care_uuid,
                    "action": "NEW"
                }
            
            ord_res = openmrs.create_order_rest(ord_payload)
            order_uuid = ord_res["uuid"]
            orders_total += ord_def["price"]
            created_orders.append({
                "uuid": order_uuid,
                "type": ord_def["type"],
                "name": ord_def["name"],
                "price": ord_def["price"]
            })
            print(f"      - Order Added: {ord_def['name']} (${ord_def['price']:.2f}) -> Order UUID: {order_uuid}")

        grand_total = p_def["consultation_fee"] + orders_total

        results.append({
            "patient_name": full_name,
            "patient_id": omrs_id,
            "patient_uuid": patient_uuid,
            "journey": p_def["journey"],
            "encounter_uuid": encounter_uuid,
            "consultation_fee": p_def["consultation_fee"],
            "orders": created_orders,
            "billable_total": grand_total
        })

    return results


def print_summary_table(results: List[Dict[str, Any]]):
    """Print an executive formatted summary table to the console."""
    print("\n" + "=" * 115)
    print(f"{'CLINICAL ECOSYSTEM SEEDING SUMMARY':^115}")
    print("=" * 115)
    header = f"{'Patient Name':<16} | {'OpenMRS ID':<10} | {'Patient UUID':<38} | {'Encounter UUID':<38}"
    print(header)
    print("-" * 115)
    for r in results:
        print(f"{r['patient_name']:<16} | {r['patient_id']:<10} | {r['patient_uuid']:<38} | {r['encounter_uuid']:<38}")
        print(f"  * Journey: {r['journey']}")
        print(f"  * Billable Lines:")
        print(f"      - Consultation Fee: ${r['consultation_fee']:.2f}")
        for ord_item in r["orders"]:
            print(f"      - {ord_item['name']} ({ord_item['type'].upper()}): ${ord_item['price']:.2f} [UUID: {ord_item['uuid']}]")
        print(f"  * Total Expected Invoice: ${r['billable_total']:.2f}")
        print("-" * 115)

    all_total = sum(r["billable_total"] for r in results)
    print(f"{'ALL PATIENTS COMBINED REVENUE PIPELINE:':<90} ${all_total:>8.2f}")
    print("=" * 115 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Seed OpenMRS 3 and Odoo 19 Healthcare Ecosystem")
    parser.add_argument("--openmrs-url", default=DEFAULT_OPENMRS_URL, help=f"OpenMRS base URL (default: {DEFAULT_OPENMRS_URL})")
    parser.add_argument("--openmrs-user", default=DEFAULT_OPENMRS_USER, help="OpenMRS admin user")
    parser.add_argument("--openmrs-pass", default=DEFAULT_OPENMRS_PASS, help="OpenMRS admin password")
    parser.add_argument("--odoo-url", default=DEFAULT_ODOO_URL, help=f"Odoo base URL (default: {DEFAULT_ODOO_URL})")
    parser.add_argument("--odoo-db", default=DEFAULT_ODOO_DB, help=f"Odoo DB (default: {DEFAULT_ODOO_DB})")
    parser.add_argument("--odoo-api-key", default=DEFAULT_ODOO_API_KEY, help="Odoo API Key")
    parser.add_argument("--skip-patients", action="store_true", help="Only setup Odoo products and stock, skip registering mock patients")

    args = parser.parse_args()

    print("==========================================================================")
    print("      HEALTHCARE INTEGRATION SEEDING SUITE: OPENMRS 3 <-> ODOO 19        ")
    print("==========================================================================")
    print(f"OpenMRS Base URL: {args.openmrs_url}")
    print(f"Odoo Base URL:    {args.odoo_url} (DB: {args.odoo_db})")

    # Initialize clients
    odoo = OdooClient(args.odoo_url, args.odoo_db, args.odoo_api_key)

    # 1. Setup Odoo Products and Inventory
    product_map = setup_odoo_products_and_inventory(odoo)

    if not args.skip_patients:
        openmrs = OpenMRSClient(args.openmrs_url, args.openmrs_user, args.openmrs_pass)
        # 2. Register Patients and Clinical Journeys in OpenMRS
        journey_results = seed_patients_and_journeys(openmrs, product_map)

        # 3. Print Summary Table
        print_summary_table(journey_results)


if __name__ == "__main__":
    main()
