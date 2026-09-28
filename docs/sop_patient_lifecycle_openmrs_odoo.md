# Standard Operating Procedure (SOP): End-to-End Patient Lifecycle Management
**Integrated Stack:** OpenMRS 3 (Clinical EHR) ↔ n8n (Integration & Orchestration Engine) ↔ Odoo 19 (Enterprise ERP / Supply Chain / Billing)  
**Target Audience:** Clinical Officers, Doctors, Triage Nurses, Hospital Cashiers, Pharmacists, System Administrators & Trainers  
**Version:** 3.0 (Production Verified & Fully Audited)

---

## 1. Executive Summary & Lifecycle Architecture

This Standard Operating Procedure (SOP) defines the operational, clinical, and financial workflow for a patient journey in an integrated healthcare facility. It reflects all end-to-end integration patterns, database guards, and protocol safeguards validated in production testing.

### System Responsibility Matrix (Separation of Concerns)

To maintain medical record integrity while leveraging ERP automation, the ecosystem enforces a strict boundary between clinical and enterprise domains:

| Domain | Source of Truth | Secondary / Follower System | Integration Mechanism & Notes |
| :--- | :--- | :--- | :--- |
| **Patient Demographics & Master Patient Index (MPI)** | **OpenMRS 3** | **Odoo 19** (`res.partner`) | MPI originates in OpenMRS (Luhn Mod-30 check-digit ID). n8n syncs demographics using batch lookup `[["ref", "in", UUIDs]]` and atomic `vals_list` to protect against connection pool exhaustion. |
| **Clinical Encounters, SOAP Notes & Diagnoses** | **OpenMRS 3** | None (Audited in Odoo `ref`) | Doctor consultation and clinical intake notes remain strictly within EHR. Odoo invoices reference OpenMRS encounter UUIDs (`OPENMRS-ENC-...`). |
| **Diagnostic Lab & Radiology Orders** | **OpenMRS 3** | **Odoo 19** (`account.move.line`) | Clinician orders tests via REST/FHIR (`ServiceRequest`). n8n pulls orders via atomic `_revinclude` and maps them to Odoo service lines (`LAB-FBC`, `RAD-XRAY-CHEST`). |
| **Pharmaceutical Prescriptions & Dispensing** | **OpenMRS 3** | **Odoo 19** (`stock.picking`, `stock.quant`) | Prescriptions originate in OpenMRS (`MedicationRequest`). Dispensing triggers an idempotent Odoo delivery order (`WH/OUT`, origin: `OPENMRS-MED-...`) deducting physical stock and tracking lots. |
| **Pharmaceutical Stock & Expiry Management** | **Odoo 19** | **OpenMRS 3** (Dispense stock view) | Physical warehouse balances, batch numbers (`stock.lot`), and expiry dates are managed in Odoo. Automatic draft Purchase Orders trigger if stock drops below 50 units. |
| **Inpatient Ward Stays & Bed Allocation** | **OpenMRS 3** | **Odoo 19** (`account.move.line`) | Inpatient admission encounters (`IMP` / `Admission`) automatically append daily ward bed charges (`INP-BED`, \$100.00/day) to the consolidated customer invoice. |
| **Customer Invoicing & Revenue Collection** | **Odoo 19** | None (Financial ledger) | Consolidated draft invoices (`account.move`) aggregate consultation fees, ward fees, lab services, and medications. Cashier registers payment via payment wizard. |
| **Financial Clearance Writeback** | **Odoo 19** | **OpenMRS 3** (`obs` Concept `162169`) | Once an invoice transitions to `payment_state = 'paid'`, n8n verifies idempotency and writes an immutable clinical clearance observation back to OpenMRS. |

---

### Visual Lifecycle Flowchart (Comprehensive 7-Phase Architecture)

![Patient Lifecycle Flowchart](./patient_lifecycle_flowchart.png)

```mermaid
flowchart TD
    subgraph P1["Phase 1: Registration & Master Patient Index (MPI)"]
        A1["OpenMRS: Register Patient (Luhn Mod-30 ID, cityVillage & UUID)"] --> A2["n8n: Demographics Sync Pipeline (Batch & vals_list)"] --> A3["Odoo 19: Create Customer Record (res.partner)"]
    end

    subgraph P2["Phase 2: Check-in & Outpatient Triage"]
        B1["OpenMRS: Start Outpatient Visit (Location: Outpatient Clinic | tag=visit)"] --> B2["OpenMRS: Record Vital Signs (BP, Pulse, Temp, BMI)"]
    end

    subgraph P3["Phase 3: Doctor Consultation & Clinical Orders"]
        C1["OpenMRS: Clinical Consultation (SOAP Notes | tag=encounter)"]
        C_rx["Prescribe Medications (MedicationRequest: Amoxicillin, Paracetamol)"]
        C_lab["Order Diagnostic Tests (ServiceRequest: LAB-FBC, Chest X-Ray)"]
        C1 --> C_rx
        C1 --> C_lab
    end

    subgraph P4["Phase 4: Pharmacy Dispensing & Stock (Outpatient Branch)"]
        D1["OpenMRS: Pharmacy App (Status = Dispensed)"] --> D2["n8n: Order-to-Stock Pipeline (Origin: OPENMRS-MED)"] --> D3["Odoo 19: Validate Delivery (WH/OUT Status: Done)"] --> D4["Odoo 19: Deduct Physical Stock (Track Lot & Expiry)"] --> D5["Odoo 19: Buffer Check (Draft RFQ if < 50)"]
    end

    subgraph P5["Phase 5: Inpatient Admission & Ward Management (Inpatient Branch)"]
        E1["OpenMRS: Inpatient Admission Order (Location: Inpatient Ward | class: IMP)"] --> E2["OpenMRS: Inpatient Bed Allocation (Bed 04 & Intake)"] --> E3["OpenMRS: Inpatient Ward Care (Daily Notes, 4-Hr Vitals & MAR)"] --> E4["n8n: Inpatient Daily Bed Fee (INP-BED $100/day + CONS-GEN $50/day)"]
    end

    subgraph P6["Phase 6: Consolidated Billing & Cashier Settlement"]
        F1["n8n: Aggregate Orders by Encounter (Atomic FHIR _revinclude)"] --> F2["Odoo 19: Customer Invoice (account.move: Consult + Bed + Lab + Meds)"] --> F3["Odoo 19: Cashier Registers Payment (Status: Paid)"] --> F4["n8n: Idempotent Clearance Writeback Engine (Pre-check GET /obs)"] --> F5["OpenMRS 3: Financial Clearance Badge (Obs Concept 162169)"]
    end

    subgraph P7["Phase 7: Discharge & Visit Closure"]
        G1["OpenMRS 3: Doctor Verifies Clearance (Signs Discharge Summary)"] --> G2["OpenMRS 3: End Active Visit (Archived & Closed)"]
    end

    A3 -->|Patient Arrives at Clinic| B1
    B2 -->|Vitals Recorded & Triaged| C1
    C_rx -->|Rx Issued (Outpatient)| D1
    C1 -.->|If Severe / Inpatient| E1
    C_lab -->|Billable Diagnostic Orders| F1
    D5 -->|Dispensed Stock Deduction| F1
    E4 -.->|Ward Bed Stays| F1
    F5 -->|Payment Verified & Cleared| G1
```

---

## 2. System Credentials & Endpoints Quick Reference

| System | Role | Access URL | Default Test Credentials | API Notes |
| :--- | :--- | :--- | :--- | :--- |
| **OpenMRS 3 Gateway** | Clinical EHR / Frontend SPA | `http://localhost:8080/openmrs/spa` | `admin` / `Admin123` | FHIR R4: `/openmrs/ws/fhir2/R4`<br/>REST: `/openmrs/ws/rest/v1` |
| **Odoo 19** | ERP / Inventory / Invoicing | `http://localhost:8069` | `admin` / `admin`<br/>(Database: `clinic_db`) | JSON-RPC 2.0: `/json/2/<model>/<method>`<br/>API Key Bearer Auth supported |
| **n8n Engine** | Integration Middleware | `http://localhost:5678` | System Admin Account | Webhooks: `/webhook/sync-patients`<br/>`/webhook/order-to-billing-and-stock` |

---

## 3. Step-by-Step Patient Lifecycle Standard Operating Procedure

### Phase 1: Patient Registration (Master Patient Index)

**Primary Actor:** Clinic Receptionist / Registration Clerk  
**Source System:** OpenMRS 3  
**Follower System:** Odoo 19 (`res.partner`)

#### Step 1.1: Register New Patient in OpenMRS
1. Log in to the OpenMRS 3 interface (`http://localhost:8080/openmrs/spa`).
2. Set session location: **Outpatient Clinic** (UUID: `44c3efb0-2583-4c80-a79e-1f756a03c0a1`).
3. Click **Add Patient** (+ icon) on the main navigation.
4. Complete demographic fields according to clinical intake requirements:
   - **Given Name:** e.g., `Sarah`
   - **Family Name:** e.g., `Wanjiku`
   - **Gender:** Female | **Birthdate:** e.g., `1992-05-15`
   - **Contact Phone:** e.g., `+254712345678`
   - **Address Line 1:** `Kenyatta Avenue, Suite 4B`
   - **City / Village:** `Nairobi` *(Note: OpenMRS REST strictly requires attribute `cityVillage`)*
   - **Country:** `Kenya`
5. Click **Register Patient**. 
   - OpenMRS allocates a verified check-digit ID from the Sequential Identifier Generator (Source UUID: `8549f706-7e85-4c1d-9424-217d50a2988b`, e.g., `10008T2`).
   - A persistent, immutable patient UUID is minted (e.g., `1b96a17b-9f9a-4716-8da0-ba537e02a0a9`).

#### Step 1.2: Demographics Synchronization to Odoo ERP
- **Automated Mode:** Workflow `WkflwSyncPat0001` executes on a 15-minute polling cron.
- **Immediate Push:** Dispatch an HTTP request to the n8n webhook:
  ```bash
  curl -X POST http://localhost:5678/webhook/sync-patients
  ```
- **Architectural Safeguards (Connection Pool Protection):**
  - Workflow performs a single batch domain query: `[["ref", "in", patient_uuids]]`.
  - Missing partners are created in one atomic call using Odoo's native `vals_list: [...]` payload.
  - Completely prevents PostgreSQL connection pool exhaustion (`psycopg2.pool.PoolError: The Connection Pool Is Full`).
- **Verification in Odoo 19:**
  1. Open Odoo (`http://localhost:8069`) $\rightarrow$ Navigate to **Contacts** (or **Invoicing** $\rightarrow$ **Customers**).
  2. Search for `Sarah Wanjiku`.
  3. Verify that:
     - **Name:** `Sarah Wanjiku`
     - **Internal Reference (`ref`):** Contains the exact OpenMRS Patient UUID.
     - **Phone:** `+254712345678`
     - **Customer Rank:** `1` (ensures immediate visibility in customer search filters).

---

### Phase 2: Check-In & Outpatient Triage (Vitals & Intake)

**Primary Actor:** Triage Nurse  
**Source System:** OpenMRS 3

#### Step 2.1: Start Outpatient Facility Visit
1. Search for Sarah Wanjiku on the OpenMRS home board.
2. Click **Start Visit**.
3. Select Facility Visit Type: **Facility Visit** (Outpatient).
4. Set location to **Outpatient Clinic** $\rightarrow$ Click **Start Visit**.
5. *Protocol Note on Visit Partitioning:* In OpenMRS FHIR2, this container is tagged `tag=visit`. The n8n billing engine purposefully filters out visit containers, preventing premature invoices from generating for pure facility check-ins.

#### Step 2.2: Record Vital Signs
1. On Sarah's patient chart, open the **Vitals & Biometrics** tab.
2. Click **Record Vitals** and input measurements:
   - **Blood Pressure:** `120/80 mmHg` (Normotensive)
   - **Pulse Rate:** `78 bpm`
   - **Temperature:** `38.2 °C` *(Triggers yellow febrile alert banner)*
   - **Respiratory Rate:** `18 breaths/min`
   - **Weight:** `65 kg` | **Height:** `168 cm` | **BMI:** `23.0 kg/m²` (Normal)
3. Click **Save**. Vitals immediately populate the clinical summary banner.

---

### Phase 3: Doctor Consultation & Clinical Ordering

**Primary Actor:** Medical Doctor / Clinical Officer  
**Source System:** OpenMRS 3

#### Step 3.1: Document Consultation & Diagnoses
1. On Sarah's chart, click **Record Encounter** $\rightarrow$ Select **Clinical Consultation**.
   - Encounter is tagged `tag=encounter` (Encounter Type UUID: `dd528487-82a5-4082-9c72-ed246bd49591`).
2. **Clinical SOAP Note:** Enter chief complaint (*"Patient presents with fever, sore throat, and cough for 3 days"*).
3. **Diagnosis:** Add `Acute Upper Respiratory Tract Infection (URTI)` (Presumed / Primary).
4. *Billing Note:* The consultation encounter automatically queues a General Consultation Fee (`CONS-GEN`, \$50.00).

#### Step 3.2: Order Diagnostic Investigations
1. On the Clinical dashboard, click **Orders** $\rightarrow$ **Add Lab/Diagnostic Order**.
2. Select investigation: **Full Blood Count (FBC)** (Concept ID: `1019AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`).
   - Mapped to Odoo product: `LAB-FBC` (\$35.00).
3. *(Optional Chest Assessment)* Order **X-Ray Chest** (Concept mapped to `RAD-XRAY-CHEST`, \$75.00).
4. Click **Sign and Save Orders**. Orders are committed via REST (`POST ws/rest/v1/order`, type: `testorder`) and automatically projected to FHIR R4 `ServiceRequest`.

#### Step 3.3: Prescribe Pharmaceuticals
1. Click **Add Medication / Prescription**.
2. Prescribe:
   - **Drug:** `Amoxicillin 250mg Suspension` (Odoo `DRUG-AMOX250`, \$12.00).
   - **Dosing Instructions:** `250 mg`, Oral, Thrice Daily (TID), 5 Days duration.
   - **Dispense Quantity:** `1 Bottle`.
3. Add symptomatic analgesic:
   - **Drug:** `Paracetamol 500mg Tabs` (Odoo `DRUG-PARA500`, \$5.00).
   - **Dispense Quantity:** `20 Tablets`.
4. Click **Sign and Save Prescriptions**. Orders are committed via REST (`type: drugorder`) and projected to FHIR R4 `MedicationRequest`.

#### Step 3.4: Clinical Pathway Decision Fork
- **Acuity Fork A (Outpatient):** Patient is stable $\rightarrow$ Proceed to **Phase 4 (Pharmacy Dispensing)**.
- **Acuity Fork B (Inpatient):** High pyrexia, dehydration, or IV requirement $\rightarrow$ Proceed to **Phase 5 (Inpatient Admission)**.

---

### Phase 4: Pharmacy Dispensing & Inventory Depletion (Outpatient Pathway)

**Primary Actor:** Hospital Pharmacist  
**Integrated Systems:** OpenMRS 3 (Pharmacy App) ↔ n8n ↔ Odoo 19 (Inventory / Warehouse)

#### Step 4.1: Dispense in OpenMRS
1. From the top navigation app launcher, select **Pharmacy**.
2. In the **Active Prescriptions** queue, select **Sarah Wanjiku**.
3. Review allergy status, drug interactions, and prescribed dosages.
4. Click the teal **Dispense** action button for Amoxicillin 250mg and Paracetamol 500mg.
5. Prescription status transitions to **Dispensed**.

#### Step 4.2: Automated ERP Stock Deduction & Verification
- **Automated Trigger:** The n8n Order-to-Billing workflow runs:
  ```bash
  curl -X POST http://localhost:5678/webhook/order-to-billing-and-stock
  ```
- **Under the Hood (Idempotency & Warehouse Picking):**
  - Origin code is locked: `OPENMRS-MED-<med_order_uuid>`.
  - Workflow checks if an existing delivery order exists in Odoo before creating.
  - Creates outgoing transfer: `stock.picking` (type: `WH/OUT`).
  - Calls `button_validate()` to transition picking state to **done**.
- **Verification in Odoo 19:**
  1. Open Odoo $\rightarrow$ **Inventory** $\rightarrow$ **Operations** $\rightarrow$ **Deliveries** (Transfers).
  2. Locate delivery order (e.g., `WH/OUT/00067`):
     - **Contact:** `Sarah Wanjiku`
     - **Source Document:** `OPENMRS-MED-<uuid>`
     - **Status:** **Done**
  3. Navigate to **Inventory** $\rightarrow$ **Products** $\rightarrow$ Select `Amoxicillin 250mg Suspension`:
     - **On Hand Quantity:** Decremented by `1.0 unit` (e.g., 98.0 $\rightarrow$ 97.0 units).
     - **Moves History:** Confirms lot deduction (`LOT-AMOX-2026-001`, expiry `2027-06-30`).
  4. *Safety Buffer Auto-Reorder:* If on-hand stock falls below **50 units**, check **Purchase** $\rightarrow$ **Requests for Quotation (RFQ)** for an automated draft PO with source `AUTO-REORDER-DRUG-AMOX250`.

---

### Phase 5: Inpatient Admission & Ward Management (Inpatient Pathway)

**Primary Actor:** Attending Physician & Inpatient Ward Nurse  
**Integrated Systems:** OpenMRS 3 (Inpatient App) ↔ n8n ↔ Odoo 19 (Billing)

#### Step 5.1: Admit Patient to Inpatient Ward
*(Follow this pathway when clinical admission is ordered)*
1. On Sarah's patient chart, click **Actions** (top right) $\rightarrow$ **Admit to Inpatient**.
2. Complete admission parameters:
   - **Admission Date/Time:** Current timestamp.
   - **Location:** **Inpatient Ward** (UUID: `ba685651-ed3b-4e63-9b35-78893060758a`).
   - **Bed Allocation:** Select available physical bed (e.g., `Bed 04 - Medical Ward`).
   - **Encounter Type:** `Admission` (UUID: `e22e39fd-7db2-45e7-80f1-60fa0d5a4378`).
3. Click **Confirm Admission**. The patient status updates to **Admitted (Inpatient)**.

#### Step 5.2: Inpatient Bed Fee Billing Resolution
- **Under the Hood (Inpatient Classification):**
  - In OpenMRS FHIR, admission encounters evaluate with `class.code = 'IMP'` (Inpatient) or encounter type `Admission`.
  - The n8n correlation engine identifies the inpatient classification.
  - Queries Odoo product catalog for product code `INP-BED` (Product ID: 61, list price: \$100.00).
  - Automatically adds the **Inpatient Daily Bed / Ward Nursing Care Fee (`INP-BED`, \$100.00/day)** alongside the **General Consultation Fee (`CONS-GEN`, \$50.00)**.
- **Verification in Odoo:**
  - Admission invoice generated with subtotal **\$150.00** (\$172.50 inclusive of 15% standard tax).
  - Both `CONS-GEN` and `INP-BED` line items confirmed on the invoice ledger.

#### Step 5.3: Ongoing Inpatient Care
1. **Nurse Ward Rounds:** Document vitals, fluid intake/output, and nursing notes every 4 hours.
2. **Inpatient MAR:** Mark scheduled doses of antibiotics as administered.

---

### Phase 6: Point-of-Care Billing & Cashier Settlement

**Primary Actor:** Hospital Cashier / Billing Accountant  
**Integrated Systems:** Odoo 19 (Invoicing) ↔ n8n ↔ OpenMRS 3 (Observations)

#### Step 6.1: Review Consolidated Patient Invoice in Odoo
1. Log into Odoo 19 $\rightarrow$ **Invoicing** $\rightarrow$ **Customers** $\rightarrow$ **Invoices**.
2. Locate the invoice for `Sarah Wanjiku` (Reference: `OPENMRS-ENC-<encounter-uuid>`).
3. Verify consolidated clinical charges:

##### Outpatient Encounter Invoice Example:
| Product Code | Description | Qty | Unit Price | Tax | Subtotal |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `CONS-GEN` | General Consultation Fee | 1.0 | \$50.00 | 15% | \$50.00 |
| `LAB-FBC` | Full Blood Count (FBC) | 1.0 | \$35.00 | 15% | \$35.00 |
| `DRUG-AMOX250` | Amoxicillin 250mg Suspension | 1.0 | \$12.00 | 15% | \$12.00 |
| **Total Amount Due** | *(Inclusive of \$14.55 Tax)* | | | | **\$111.55** |

##### Inpatient Admission Invoice Example:
| Product Code | Description | Qty | Unit Price | Tax | Subtotal |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `CONS-GEN` | General Consultation Fee | 1.0 | \$50.00 | 15% | \$50.00 |
| `INP-BED` | Inpatient Ward Bed & Nursing Care | 1.0 | \$100.00 | 15% | \$100.00 |
| **Total Amount Due** | *(Inclusive of \$22.50 Tax)* | | | | **\$172.50** |

#### Step 6.2: Confirm & Register Payment
1. Review invoice line accuracy; click **Confirm** (`action_post()`). Invoice status transitions to **Posted**.
2. Click **Register Payment**:
   - **Payment Wizard:** `account.payment.register`
   - **Journal:** `Cash` (or `Bank` / `Mobile Money`)
   - **Amount:** Full balance (\$111.55 or \$172.50)
3. Click **Create Payment**.
4. Invoice state displays a green **Paid** badge (`payment_state = 'paid'`).

#### Step 6.3: Automated Idempotent Clearance Writeback
- **Trigger:** Dispatch order workflow or wait for 5-minute poll:
  ```bash
  curl -X POST http://localhost:5678/webhook/order-to-billing-and-stock
  ```
- **Idempotency Guard Implementation:**
  - n8n identifies paid invoice (`payment_state in ['paid', 'in_payment']`).
  - Pre-Check Node queries OpenMRS:
    `GET ws/rest/v1/obs?encounter=<uuid>&concept=162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
    *(Configured with expression prefix: `=http://openmrs-gateway/...`)*
  - If a clearance observation already exists, the item is dropped from the creation queue.
  - If unrecorded, n8n posts an observation:
    - **Concept:** `162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` ("Text of encounter note")
    - **Value:** `"Financial Clearance: Payment Settled (Odoo Invoice: INV/2026/00014)"`
  - **Guaranteed Idempotency:** Triggering the workflow 1 time or 100 times results in **exactly 1 observation** in OpenMRS (`IDEMPOTENT: True`).
- **Verification in OpenMRS:**
  1. Open Sarah's chart in OpenMRS.
  2. Under **Recent Results / Observations**, confirm presence of:  
     `Text of encounter note: Financial Clearance: Payment Settled (Odoo Invoice: INV/2026/00014)`

---

### Phase 7: Clinical Discharge & Visit Closure

**Primary Actor:** Attending Physician & Discharge Desk  
**Source System:** OpenMRS 3

#### Step 7.1: Clinical Discharge
1. The physician opens Sarah's chart and verifies the **Payment Settled: CLEARED** note.
2. *Discharge Safety Rule:* Hospital policy strictly prohibits clinical discharge without verified payment clearance.
3. In the patient dashboard, click **Discharge / Clinical Summary**:
   - **Discharge Disposition:** `Discharged Home`
   - **Discharge Instructions:** *"Acute tonsillitis resolved. Complete oral course of Amoxicillin. Return if fever recurs."*
4. Click **Save and Discharge**.

#### Step 7.2: End Active Visit
1. Under the active visit banner (right sidebar), click **End Visit**.
2. Confirm visit end time.
3. Patient visit is closed. The clinical record remains permanently archived in OpenMRS, while accounting, tax, and inventory balances remain reconciled in Odoo 19.

---

## 4. Role-Based Training & Demonstration Script

Use this structured script when demonstrating the integrated ecosystem or onboarding clinical and administrative personnel:

```
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| TIME  | ACTOR            | ACTION & SYSTEM                               | KEY TALKING POINT                          |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 00:00 | Receptionist     | OpenMRS: Register Patient Sarah Wanjiku       | "Single Master Patient Index. Generates    |
|       |                  | curl /webhook/sync-patients                   | verified Luhn Mod-30 check-digit ID and    |
|       |                  | Odoo: Show Sarah in Contacts with UUID ref    | batches sync to Odoo with zero pool drops."|
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 03:00 | Triage Nurse     | OpenMRS: Start Outpatient Visit (Facility)    | "Visit container is partitioned so check-in|
|       |                  | OpenMRS: Record Vitals (BP, Temp 38.2°C)      | does not prematurely trigger billings."    |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 06:00 | Medical Doctor   | OpenMRS: Consultation Encounter (SOAP note)   | "Doctor focuses 100% on patient care.      |
|       |                  | OpenMRS: Order FBC Lab + Amoxicillin 250mg    | Atomic FHIR revincludes capture every lab  |
|       |                  | OpenMRS: Sign and Save Orders                 | and medication order without blind spots." |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 10:00 | Pharmacist       | OpenMRS: Pharmacy App -> Click Dispense       | "Clinical dispense automatically triggers  |
|       |                  | curl /webhook/order-to-billing-and-stock      | warehouse WH/OUT delivery, decrements      |
|       |                  | Odoo: Delivery Order Done & Lot Deducted      | physical stock, and audits lot expiries."  |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 14:00 | (Optional Inpt)  | OpenMRS: Admit to Inpatient Ward (Bed 04)     | "Admission encounter dynamically appends   |
|       | Doctor / Nurse   | Odoo: Show Inpatient Invoice (Bed $100 + $50) | daily ward bed fee ($100) to invoice."     |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 18:00 | Hospital Cashier | Odoo: Open Invoices -> Sarah Wanjiku          | "Single consolidated invoice combines     |
|       |                  | Odoo: Confirm ($111.55) & Register Payment    | consultation, labs, meds, and ward stays.  |
|       |                  | OpenMRS: Show Financial Clearance Badge       | Payment triggers idempotent writeback."    |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
| 22:00 | Attending Doctor | OpenMRS: Verify Payment Settled Note          | "Discharge safety enforcement: Doctor      |
|       |                  | OpenMRS: Signs Discharge & Ends Visit         | verifies clearance before closing visit."  |
+-------+------------------+-----------------------------------------------+--------------------------------------------+
```

---

## 5. Verification Checklist & Troubleshooting Guide

| Checkpoint | Expected Condition | Diagnostic Procedure & Root Cause Fix |
| :--- | :--- | :--- |
| **Patient Sync & Demographics** | `res.partner` created in Odoo with `ref = OpenMRS UUID` | If 400 in OpenMRS: Ensure address uses `cityVillage` (not `city`). If 500 in Odoo: Verify workflow batches lookup `[["ref", "in", UUIDs]]` to avoid connection pool exhaustion. |
| **Atomic Order Capture** | Consultation, Lab (`LAB-FBC`), and Drug (`DRUG-AMOX250`) appear on invoice | Check n8n `Fetch Encounters` URL: Must include `_revinclude=ServiceRequest:encounter&_revinclude=MedicationRequest:encounter` to bypass FHIR ServiceRequest sort limitations. |
| **Stock Delivery (WH/OUT)** | Delivery order state is `done`; stock decremented | Verify product code matches `default_code`. Ensure picking confirmation uses integer picking ID before calling `button_validate()`. |
| **Inpatient Bed Fee Billing** | Inpatient invoice includes both `CONS-GEN` (\$50) and `INP-BED` (\$100) | Check that `INP-BED` is in product query domain and encounter has `class = 'IMP'` or encounter type `Admission`. |
| **Financial Clearance Idempotency** | Exactly 1 clearance observation exists in OpenMRS across repeated runs | Ensure n8n `Check Existing Clearance Obs` URL starts with `=` (`=http://openmrs-gateway/...`) so expression evaluates dynamically. |
| **OpenMRS Observation Format** | Concept `162169AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` tagged with Odoo invoice | Verify OpenMRS basic auth credential (`NV3zllgqUv388ppB`). Check that observation payload includes `person`, `encounter`, and `obsDatetime`. |

---

## 6. Regulatory, Audit & Best Practice Standards

1. **Universal Foreign Key (`ref`):** Never edit or overwrite the `ref` field on `res.partner` or `account.move` in Odoo. The OpenMRS UUID is the universal foreign key linking clinical health records to accounting ledgers.
2. **Pharmaceutical Lot Traceability:** Every storable pharmaceutical product inwarded into Odoo must have an associated pharmaceutical lot number and expiration date to satisfy Good Pharmacy Practice (GPP) and MOH regulatory audits.
3. **Idempotency by Design:** All integration pipelines enforce pre-checks on entity creation (`origin` for stock pickings, `ref` for customer invoices, and concept `162169` for clearance observations). This guarantees zero duplicate financial or inventory records.
4. **Data Privacy (HIPAA / GDPR Compliance):** All container traffic runs across an isolated Docker bridge network (`health-network`). Sensitive patient clinical diagnoses remain strictly within OpenMRS; only billable concept codes and administrative demographics pass to Odoo ERP.
