# New Revisions

To transform these changes into a functional technical workflow, the system must pivot from a document-centric approach to a data-stream approach. This workflow prioritizes the structured data within the FINRA CRD (Central Registration Depository) and SEC databases to ensure accuracy while minimizing the costs associated with PDF scanning.

## 1. Revision 1: Technical Workflow and Decision Logic

### 1.1 Data Ingestion Engine (The "Tri-Stream" Feed)

Instead of a single crawler, the system operates three distinct streams to gather the specific fields requested:

- **Stream A: FINRA API (The Operations Feed):** Pulls structured JSON data for **Types of Business**, **Direct Owners**, and **Executive Officers**. This replaces manual PDF reading for the upper-right quadrant.
- **Stream B: SEC EDGAR (The Financial Feed):** Retrieves the raw **FOCUS Reports (Form X-17A-5)** to extract **Net Capital** and the CEO’s direct contact information listed on the filing.
- **Stream C: Relationship Parser:** Scans the specific text strings in the **Firm Operations** section to apply the new **Self-Clearing vs. Introducing** logic.

### 1.2 Logic and Rule-Set Layer (The "Intelligence" Hub)

Once the data is ingested, the system applies the new automated definitions:

#### 1.2.1 Self-Clearing Logic Gate

- **IF** the record states: *"This firm does not hold or maintain funds or securities or provide clearing services"* **AND** *"This firm does not refer or introduce customers"*.
- **THEN** label the firm as **True Self-Clearing** *(high-value target for outsourcing services)*.

#### 1.2.2 Introducing Logic Gate

- **IF** the record states: *"This firm does refer or introduce customers to other brokers and dealers"*.
- **THEN** pull the listed **Clearing Partner** (for example, **Apex** or **Pershing**) from the associated table.

#### 1.2.3 Business-Type Flagging System

- The system scans the **Types of Business** list.
- If **Private Placement Only** or **Investment Advisory** are the sole business types, it applies a **Niche/Restricted** flag to the dashboard so users can skip or qualify those firms instantly.

## 2. Revision 2: Dashboard Mapping and Monitoring Workflow

### 2.1 Updated UI Dashboard Mapping

The workflow output is mapped to the four quadrants of the dashboard:

| Quadrant | Content Source | Purpose |
|---|---|---|
| **Top-Left: Financials** | SEC FOCUS Reports | Displays **Net Capital**, **Excess Capital**, and **YoY Growth** trend lines. |
| **Top-Right: Assessment** | FINRA Detailed Report Overview | Summarizes the firm profile, website, and address. |
| **Operations** | Types of Business and total count | Supports operational qualification and firm categorization. |
| **Bottom-Left: People** | FOCUS Report & CRD Owners | Displays names and positions of owners, along with the CEO’s name, phone number, and email. |
| **Bottom-Right: Relationship** | Clearing/Introducing Section | Identifies current clearing partners and maps the **Introducing** relationship. |

### 2.2 Continuous Monitoring and Alerting

The final part of the workflow ensures the data remains current without manual intervention:

- **Daily Monitor:** Scans for new **Form 17a-11** filings. If a firm is flagged for capital deficiency, the workflow automatically moves it to the **Alternative List** and turns its health badge red.
- **Bi-Monthly Refresh:** Re-runs the **FINRA API stream** to capture changes in **Direct Owners** or **Business Types**.
- **Triggered Enrichment:** When a user clicks a firm, the system performs a real-time **Health Check** to determine whether the contact information or net capital must be refreshed from the latest **SEC filing**.
