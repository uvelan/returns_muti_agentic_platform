"""The Ferguson idiom the seed corpus is generated in, mined from real order lines.

Nothing here was fetched from fergusonhome.com or any other website. Every shape
in this module was derived from data already in `return_source`: the 101 real
`salesInv` documents (572 order lines, 482 distinct `masterProductId`) and the
one real `lkpSearchProduct` document, `2175168` / `PSRGW1212`.

What the real lines actually establish, and therefore what is reproduced:

* **SKU is a vendor product code, not a house number.** `Q1685`,
  `R7010108781`, `ADWVCFAMRPM`, `A5A7A4048A1000A`, `PSRGW1212`, `BRE250T61NCWW`.
  Four shapes cover the observed set and `SKU_SHAPES` generates in those four.
  A single `SKU%07d` format -- what the previous generator emitted -- makes every
  scan-the-box search look uniform in a way the real catalogue is not.
* **`productDesc` is ERP-abbreviated, uppercase, and size-led.**
  `1-1/2 ABS DWV COUP`, `16X25 SILV FLEX AIR DUCT R8.0`,
  `12X12 RG RTN AIR GRL 1/2 FIN WHIT`. Size, then material/spec, then an
  abbreviated noun. `prodLongDesc` and `webDisplayName` expand the same string
  into words, because `productDesc` is too abbreviated for anyone to search.
* **Products belong to a department.** The one real product carries
  `deptCodeDesc: "HVAC - AIR DIST"`. Departments here are the trade's, and each
  category names its own, so a department is never at odds with the noun.
* **A finish belongs to some categories and not others.** The real product
  carries `eco.colorFinish: ["White"]`. A lavatory faucet has a finish; a flex
  duct does not, and a run of PVC does not. `Category.finishes` is empty for
  every category where inventing one would be fabrication, and the finish
  vocabulary is the trade's real one.
* **`lineType` is `MP` 499 / `C` 39 / `CB` 24 / `SP` 8 / `NA` 1 / `F` 1** across
  the 572 real lines, and `LINE_TYPE_WEIGHTS` reproduces that distribution
  rather than picking uniformly.

The name pools are the other half. Every customer in this corpus is an
individual person -- an operator decision, applied to the real records too --
with an email derived from the name, so a search on either resolves the same
customer.
"""

from __future__ import annotations

from dataclasses import dataclass

# fmt: off
# The tables below are data, laid out by hand so a reader can see the shape of
# each pool at a glance. Auto-formatting them to one item per line turns four
# screens of vocabulary into forty and hides exactly the distributions this
# module exists to record.

# ---------------------------------------------------------------------------
# People
#
# Ordinary given names and surnames that read as real without belonging to
# anyone. Broad enough that 1,000 customers do not become a hundred variations
# of one surname: 128 x 208 is 26,624 combinations, drawn without replacement.
# ---------------------------------------------------------------------------

GIVEN_NAMES: tuple[str, ...] = (
    "Aaron", "Adam", "Adrian", "Alan", "Alberto", "Alexis", "Alicia", "Allison",
    "Amanda", "Andre", "Angela", "Anita", "Anthony", "Antonio", "April", "Arthur",
    "Ashley", "Audrey", "Austin", "Barbara", "Beatriz", "Benjamin", "Bernard",
    "Beverly", "Blake", "Bradley", "Brandon", "Brenda", "Brian", "Bridget",
    "Bruce", "Bryan", "Caleb", "Camille", "Carla", "Carlos", "Carmen", "Carol",
    "Casey", "Cecilia", "Charles", "Cheryl", "Christina", "Christopher", "Claire",
    "Clarence", "Colleen", "Connie", "Corey", "Craig", "Curtis", "Damon", "Daniel",
    "Danielle", "Darlene", "Darrell", "David", "Dawn", "Dean", "Deborah", "Dennis",
    "Derek", "Diana", "Dmitri", "Dolores", "Donald", "Donna", "Douglas", "Duane",
    "Dustin", "Edward", "Eileen", "Elaine", "Elena", "Elijah", "Emily", "Eric",
    "Erika", "Ernest", "Esther", "Eugene", "Evelyn", "Farah", "Felix", "Fiona",
    "Frances", "Francisco", "Frank", "Gabriel", "Gail", "Gerald", "Gina", "Glenn",
    "Gordon", "Grant", "Gregory", "Harold", "Hector", "Heidi", "Helen", "Henry",
    "Hilary", "Howard", "Ian", "Imani", "Ingrid", "Irene", "Isaac", "Ivan",
    "Jacqueline", "Jamal", "James", "Janet", "Jared", "Jasmine", "Jason", "Javier",
    "Jeanette", "Jeffrey", "Jenna", "Jeremy", "Jerome", "Jessica", "Joan", "Joel",
    "Johanna", "Jonathan", "Jordan", "Jorge", "Joseph", "Joyce", "Juanita",
    "Julian", "Justin", "Karen", "Katherine", "Keith", "Kelvin", "Kenneth",
    "Kevin", "Kimberly", "Kurt", "Kyle", "Lance", "Larry", "Latoya", "Laura",
    "Lawrence", "Leah", "Lena", "Leonard", "Leslie", "Linda", "Lionel", "Lisa",
    "Lorraine", "Lucas", "Luis", "Lydia", "Malik", "Marcia", "Marcus", "Margaret",
    "Maria", "Marilyn", "Mario", "Marlon", "Marshall", "Martin", "Marvin",
    "Matthew", "Maureen", "Melanie", "Melvin", "Michael", "Michelle", "Miguel",
    "Miriam", "Monica", "Nadia", "Nancy", "Nathan", "Neil", "Nicole", "Noel",
    "Norma", "Olivia", "Omar", "Oscar", "Pamela", "Patricia", "Patrick", "Paul",
    "Pedro", "Peter", "Philip", "Phyllis", "Priya", "Rachel", "Rafael", "Ralph",
    "Ramon", "Randall", "Raymond", "Rebecca", "Regina", "Renee", "Ricardo",
    "Richard", "Rita", "Robert", "Roberto", "Rodney", "Roger", "Ronald", "Rosa",
    "Roy", "Russell", "Ruth", "Ryan", "Samuel", "Sandra", "Sean", "Sergio",
    "Sharon", "Sheila", "Shirley", "Sidney", "Simone", "Stanley", "Stephanie",
    "Steven", "Susan", "Sylvia", "Tamara", "Terrence", "Teresa", "Thelma",
    "Theodore", "Thomas", "Tiffany", "Timothy", "Tina", "Todd", "Tracy", "Travis",
    "Trevor", "Tyrone", "Valerie", "Vanessa", "Vernon", "Veronica", "Victor",
    "Vincent", "Virginia", "Wallace", "Walter", "Wanda", "Warren", "Wayne",
    "Wendy", "Wesley", "Willie", "Yolanda", "Yuki", "Yvonne", "Zachary",
)

SURNAMES: tuple[str, ...] = (
    "Abbott", "Acosta", "Adeyemi", "Aguilar", "Alvarado", "Ambrose", "Anders",
    "Archer", "Arnett", "Ashby", "Atwater", "Bailey", "Baldwin", "Ballard",
    "Banks", "Barlow", "Barnett", "Barrett", "Bass", "Baxter", "Beasley",
    "Beckett", "Bellamy", "Benitez", "Bhatt", "Blackwell", "Blanchard", "Bolton",
    "Bonner", "Booker", "Boone", "Bowers", "Boyle", "Bradshaw", "Brennan",
    "Bridges", "Brock", "Buckley", "Burgess", "Burkhart", "Cabrera", "Caldwell",
    "Calhoun", "Callahan", "Camacho", "Cardenas", "Carlisle", "Carrington",
    "Castellanos", "Chandler", "Chapman", "Chavez", "Childers", "Clayton",
    "Clifton", "Cochran", "Coffey", "Coleman", "Conley", "Conrad", "Copeland",
    "Cortez", "Cousins", "Crawford", "Crowder", "Cummings", "Dalton", "Daugherty",
    "Davenport", "Delacroix", "Delgado", "Devlin", "Dickerson", "Dillard",
    "Dockery", "Donnelly", "Dorsey", "Doyle", "Draper", "Duffy", "Dunlap",
    "Eastman", "Eckhart", "Ellison", "Emerson", "Escobar", "Estrada", "Fairbanks",
    "Farrell", "Feldman", "Ferraro", "Fitzgerald", "Fletcher", "Flynn", "Fontaine",
    "Forrester", "Foulkes", "Fowler", "Frazier", "Fuentes", "Gallagher", "Gallardo",
    "Garrison", "Gentry", "Gibbons", "Gilliam", "Godfrey", "Goodwin", "Granger",
    "Greer", "Griffith", "Guerrero", "Hadley", "Haggerty", "Halloran", "Hampton",
    "Hancock", "Hardin", "Harlow", "Hartley", "Hastings", "Hathaway", "Hawkins",
    "Hayden", "Hendricks", "Herrera", "Hickman", "Hollingsworth", "Holloway",
    "Hooper", "Hopkins", "Horton", "Huffman", "Hutchins", "Ibarra", "Ingram",
    "Jarvis", "Jennings", "Kaminski", "Keegan", "Kendrick", "Kilgore", "Kirkland",
    "Knapp", "Kowalski", "Lambert", "Landry", "Langston", "Lattimore", "Ledford",
    "Leonard", "Lindqvist", "Livingston", "Lockhart", "Lombardi", "Lyons",
    "Macklin", "Maddox", "Mahoney", "Maldonado", "Mallory", "Marchetti", "Marsh",
    "Mathis", "Mbeki", "McAllister", "McCabe", "McCrary", "McKenna", "Meacham",
    "Medina", "Mendoza", "Merrick", "Milburn", "Monroe", "Montague", "Moreau",
    "Mortimer", "Nakamura", "Navarro", "Newcomb", "Ngata", "Nicholson", "Novak",
    "Oakley", "Okonkwo", "Ortega", "Osborne", "Paxton", "Pemberton", "Perkins",
    "Petrov", "Pettigrew", "Pruitt", "Quintero", "Radcliffe", "Rahman", "Ramsey",
    "Randolph", "Rankin", "Redmond", "Reyes", "Reynolds", "Rhodes", "Riddle",
    "Rivas", "Roark", "Rockwell", "Rosales", "Rutherford", "Salazar", "Sandoval",
    "Sargent", "Schuyler", "Sheridan", "Sinclair", "Sorensen", "Spearman",
    "Stallworth", "Stanton", "Steele", "Sterling", "Stoddard", "Strickland",
    "Sutcliffe", "Swanson", "Tanaka", "Tatum", "Thornbury", "Tillman", "Torres",
    "Trujillo", "Underhill", "Vance", "Vandenberg", "Vasquez", "Vaughn",
    "Villanueva", "Wadsworth", "Waller", "Weatherby", "Whitfield", "Whitmore",
    "Wilkerson", "Winslow", "Wolcott", "Woodard", "Yeager", "Zamora",
)

# ---------------------------------------------------------------------------
# Places. City, state and ZIP prefix are drawn together, so no customer is ever
# in "Dallas, VT 90210" -- a geographically impossible address makes a location
# search look broken when it is the data that is wrong.
# ---------------------------------------------------------------------------

PLACES: tuple[tuple[str, str, str], ...] = (
    ("AKRON", "OH", "443"), ("ALBUQUERQUE", "NM", "871"), ("ARLINGTON", "TX", "760"),
    ("ATLANTA", "GA", "303"), ("AUGUSTA", "GA", "309"), ("AUSTIN", "TX", "787"),
    ("BAKERSFIELD", "CA", "933"), ("BALTIMORE", "MD", "212"), ("BOISE", "ID", "837"),
    ("CHARLOTTE", "NC", "282"), ("CHATTANOOGA", "TN", "374"), ("CHICAGO", "IL", "606"),
    ("CINCINNATI", "OH", "452"), ("COLUMBUS", "OH", "432"), ("DALLAS", "TX", "752"),
    ("DENVER", "CO", "802"), ("DES MOINES", "IA", "503"), ("ELK GROVE", "CA", "956"),
    ("FRESNO", "CA", "937"), ("GARDEN GROVE", "CA", "928"), ("GREENSBORO", "NC", "274"),
    ("HOUSTON", "TX", "770"), ("INDIANAPOLIS", "IN", "462"), ("JACKSONVILLE", "FL", "322"),
    ("KANSAS CITY", "MO", "641"), ("KNOXVILLE", "TN", "379"), ("LAKEWOOD", "CO", "802"),
    ("LAS VEGAS", "NV", "891"), ("LEXINGTON", "KY", "405"), ("LOUISVILLE", "KY", "402"),
    ("MARTINEZ", "GA", "309"), ("MEMPHIS", "TN", "381"), ("MESA", "AZ", "852"),
    ("MOBILE", "AL", "366"), ("NASHVILLE", "TN", "372"), ("OMAHA", "NE", "681"),
    ("ORLANDO", "FL", "328"), ("PHOENIX", "AZ", "850"), ("PLYMOUTH", "MN", "554"),
    ("PORTLAND", "OR", "972"), ("RALEIGH", "NC", "276"), ("RENO", "NV", "895"),
    ("RICHMOND", "VA", "232"), ("SACRAMENTO", "CA", "958"), ("SAINT LOUIS PARK", "MN", "554"),
    ("SALT LAKE CITY", "UT", "841"), ("SAN ANTONIO", "TX", "782"), ("SEATTLE", "WA", "981"),
    ("SPOKANE", "WA", "992"), ("SPRINGFIELD", "MO", "658"), ("TACOMA", "WA", "984"),
    ("TAMPA", "FL", "336"), ("TEMPE", "AZ", "852"), ("TULSA", "OK", "741"),
    ("WICHITA", "KS", "672"), ("WINSTON SALEM", "NC", "271"),
)

STREET_NAMES: tuple[str, ...] = (
    "ALDERMAN", "BEACON", "BEAUFORT", "BEVERLY", "CALLOWAY", "CHANDLER", "CORBIN",
    "DUNMORE", "FOUNDRY", "HALSTEAD", "INDUSTRIAL", "KESTREL", "LARKSPUR",
    "MERIDIAN", "MULBERRY", "OXFORD", "PINEWOOD", "RAVENSWOOD", "SYCAMORE",
    "TRENHOLM", "WHITCOMB", "WINSLOW",
)

STREET_TYPES: tuple[str, ...] = ("ST", "AVE", "RD", "BLVD", "DR", "WAY", "LN", "CT", "PKWY")

# ---------------------------------------------------------------------------
# Order header vocabulary, taken from the 101 real orders.
# ---------------------------------------------------------------------------

#: Real branch account ids, weighted by how often they appear on a real order.
#: These are branch codes, not company names, so they carry no customer identity.
BRANCH_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("LAKEWOOD", 14), ("SACRAMENTO", 12), ("LENZ", 11), ("PHOENIX", 10),
    ("GARDEN", 9), ("CHARLOTTE", 7), ("NASH", 7), ("ORL", 7), ("OHVAL", 6),
    ("PLYMOUTH", 6), ("SEATTLE", 4), ("DALLAS", 3), ("MINNWW", 3), ("DIST", 3),
    ("ARIZONAWW", 1), ("MIDATLIND", 1), ("COL", 1),
)

#: Real order-number prefixes. `salesCode` is `CS` on 100 of 101 real orders --
#: this extract is a cash-sale slice -- and the prefix is a two-letter series.
ORDER_PREFIXES: tuple[str, ...] = (
    "CA", "CC", "CD", "CE", "CF", "CG", "CH", "CI", "CJ", "CK", "CL", "CN",
    "CO", "CP", "CQ", "CR", "CS", "CT", "CV", "CW", "CX", "CZ",
)

ORDER_STATUS_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("CALLCSR", 58), ("INVOICED", 39), ("INVOICE-PAID", 2), ("READY-FOR-PICKUP", 1),
)

SALES_TYPE_WEIGHTS: tuple[tuple[str, int], ...] = (("CASH", 59), ("INV", 41))

#: `shipViaCode` -> `shipViaDesc`, weighted as observed. CPU is counter pickup,
#: which is why more than half of a cash-sale extract carries it.
SHIP_VIA: tuple[tuple[str, str, int], ...] = (
    ("CPU", "CUSTOMER PICKUP", 56),
    ("WCL", "WILL CALL", 29),
    ("OT", "OUR TRUCK", 13),
    ("XPW", "EXPRESS PARCEL", 1),
    ("M", "MAIL", 1),
)

#: The invoice statuses, in their observed proportion to each other. What an
#: order forced into the delivered cohort draws from: delivery requires an
#: invoice order code, so `CALLCSR` and `READY-FOR-PICKUP` cannot appear there.
INVOICED_STATUS_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("INVOICED", 39), ("INVOICE-PAID", 2),
)

#: The ship-via codes that are driven rather than collected, in their observed
#: proportion. `CPU` and `WCL` are absent by definition -- an order collected at
#: a counter is never delivered.
DELIVERY_SHIP_VIA_WEIGHTS: tuple[tuple[tuple[str, str], int], ...] = (
    (("OT", "OUR TRUCK"), 13), (("XPW", "EXPRESS PARCEL"), 1), (("M", "MAIL"), 1),
)

#: Real inventory warehouse ids from the order lines, weighted as observed.
WAREHOUSE_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("3526", 48), ("686", 42), ("603", 28), ("596", 26), ("2624", 21), ("31", 20),
    ("1556", 20), ("1549", 19), ("2", 18), ("133", 16), ("2028", 15), ("1844", 14),
    ("1969", 12), ("37", 11), ("1441", 10), ("195", 10), ("1950", 10), ("144", 10),
    ("1781", 9), ("2680", 9), ("795", 8), ("1305", 7), ("2210", 6), ("428", 5),
)

#: `lineType` across the 572 real lines. `MP` dominates; the rest are comment,
#: charge-back, special and non-stock lines. See the execution state's BLOCKED
#: section for why `SP` is not read as a stock classification.
LINE_TYPE_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("MP", 499), ("C", 39), ("CB", 24), ("SP", 8), ("NA", 1), ("F", 1),
)

#: Lines per order, the real empirical multiset. The distribution is heavily
#: skewed -- 29 of 101 orders carry a single line and one carries 49 -- so
#: drawing uniformly from a range would produce a corpus in which single-line
#: orders, the commonest return case, are rare.
LINES_PER_ORDER: tuple[int, ...] = (
    *([1] * 29), *([2] * 19), *([3] * 12), *([4] * 6), *([5] * 6), *([6] * 2),
    *([7] * 5), *([8] * 4), *([9] * 3), *([10] * 2), 12, 15, 15, 16, 19, 20, 21, 21,
)

PO_NUMBERS: tuple[str, ...] = (
    "SHOP STOCK", "TRUCK STOCK", "SERVICE CALL", "WAREHOUSE", "JOB", "STOCK",
    "REPLACEMENT", "WARRANTY", "MAINTENANCE", "RESIDENTIAL", "NEW CONSTRUCTION",
)

#: Ferguson associates, not customers. Kept because the real header carries a
#: writer and a salesman and an order with neither reads as machine-generated.
ASSOCIATES: tuple[str, ...] = (
    "LANE DOZIER", "MARIA OCHOA", "DERRICK HALL", "SUSAN PARRISH", "TONY BELLINI",
    "KAREN MOSS", "RAY GUTHRIE", "PATRICE LUNDY", "COLE HARRINGTON", "JEAN NOLAN",
)

# ---------------------------------------------------------------------------
# The product catalogue.
#
# Finishes are the trade's, and a category has them only where a finish is a
# real attribute of the goods. Emptiness is the point: a flex duct, a run of
# PVC and a bag of crimp rings have no finish, and giving every product one
# would be fabricating an attribute so that a field looks populated.
# ---------------------------------------------------------------------------

#: Plated and coated metal finishes, on trim an installer chooses by look.
METAL_FINISHES: tuple[tuple[str, str], ...] = (
    ("Polished Chrome", "CP"),
    ("Brushed Nickel", "BN"),
    ("Matte Black", "MB"),
    ("Oil Rubbed Bronze", "ORB"),
    ("Stainless", "SS"),
)

#: Vitreous china and seat colours. A different vocabulary from plated trim,
#: and mixing the two would put "oil rubbed bronze" on a toilet.
CHINA_COLOURS: tuple[tuple[str, str], ...] = (
    ("White", "WHT"), ("Biscuit", "BIS"), ("Bone", "BONE"), ("Black", "BLK"),
)

#: Grille, register and diffuser finishes. The one real product in the extract
#: is `12X12 RG RTN AIR GRL 1/2 FIN WHIT` with `eco.colorFinish: ["White"]`.
GRILLE_FINISHES: tuple[tuple[str, str], ...] = (
    ("White", "WHIT"), ("Brown", "BRN"), ("Sandtone", "SAND"),
)

#: Brushed and mirror finishes on sinks and stainless fabrications.
SINK_FINISHES: tuple[tuple[str, str], ...] = (
    ("Stainless", "SS"), ("Brushed Nickel", "BN"),
)


@dataclass(frozen=True)
class Category:
    """One family of goods, and everything a plausible line item needs.

    `finishes` empty means the goods have no finish, and no `eco.colorFinish`
    is written. That is a statement about the trade, not a gap in the data.
    """

    key: str
    department: str
    noun: str
    long_noun: str
    vendors: tuple[str, ...]
    sizes: tuple[str, ...]
    specs: tuple[str, ...]
    unit_of_measure: str
    price_range: tuple[float, float]
    finishes: tuple[tuple[str, str], ...] = ()
    sku_shape: str = "alpha_numeric"


#: Expansions for the ERP's abbreviations. `productDesc` reads
#: `16X25 SILV FLEX AIR DUCT R8.0`; nobody types that into a search box, so
#: `webDisplayName` and `prodLongDesc` carry the expanded form.
ABBREVIATIONS: dict[str, str] = {
    "ABS": "ABS", "ADPT": "Adapter", "AIR": "Air", "ALUM": "Aluminium",
    "ANG": "Angle", "ASSY": "Assembly", "BALL": "Ball", "BEND": "Bend",
    "BIS": "Biscuit", "BLK": "Black", "BN": "Brushed Nickel", "BOOT": "Boot",
    "BRN": "Brown", "BRS": "Brass", "BV": "Ball Valve", "CAP": "Cap",
    "CHK": "Check", "CI": "Cast Iron", "CLMP": "Clamp", "CMNT": "Cement",
    "COMP": "Compression", "COND": "Condenser", "COP": "Copper", "COUP": "Coupling",
    "CP": "Polished Chrome", "CRMP": "Crimp", "CXC": "C x C", "DMPR": "Damper",
    "DUCT": "Duct", "DWV": "DWV", "ELL": "Elbow", "ESC": "Escutcheon",
    "EXP": "Expansion", "FCT": "Faucet", "FG": "Filter Grille", "FIN": "Fin",
    "FIP": "FIP", "FLEX": "Flexible", "FLG": "Flange", "FLTR": "Filter",
    "FURN": "Furnace", "GA": "Gauge", "GALV": "Galvanised", "GATE": "Gate",
    "GBL": "Globe", "GRL": "Grille", "HDL": "Handle", "HGR": "Hanger",
    "HOSE": "Hose", "IPS": "IPS", "KIT": "Kit", "LAV": "Lavatory", "LF": "Lead Free",
    "MB": "Matte Black", "MI": "Malleable Iron", "MTR": "Motor", "NIP": "Nipple",
    "ORB": "Oil Rubbed Bronze", "PEX": "PEX", "PIPE": "Pipe", "PLAS": "Plastic",
    "PUMP": "Pump", "PVC": "PVC", "RED": "Reducer", "REG": "Register",
    "RNG": "Ring", "RTN": "Return", "S40": "Schedule 40", "SAN": "Sanitary",
    "SEAT": "Seat", "SHWR": "Shower", "SILV": "Silver", "SS": "Stainless",
    "STL": "Steel", "STP": "Stop", "STRN": "Strainer", "SWT": "Sweat",
    "TANK": "Tank", "TEE": "Tee", "THRD": "Threaded", "TRAP": "Trap",
    "TSTAT": "Thermostat", "TUBE": "Tube", "UNIT": "Unit", "VENT": "Vent",
    "VLV": "Valve", "WHIT": "White", "WHT": "White", "WHTR": "Water Heater",
    "WROT": "Wrot", "WYE": "Wye", "WM": "Wall Mount", "ZN": "Zinc",
    "ADJ": "Adjustable", "ALT": "Alternate", "BRZ": "Bronze", "BT": "Bolt",
    "CTRSET": "Centerset", "CLNOUT": "Cleanout", "DIV": "Diverter", "EL": "Elongated", "FSK": "FSK",
    "GAL": "Gallon", "GPF": "GPF", "GPM": "GPM", "HD": "Heavy Duty",
    "HORIZ": "Horizontal", "HP": "HP", "KBTU": "KBTU", "LP": "Propane",
    "MBH": "MBH", "MERV": "MERV", "MNT": "Mount", "NB": "Nickel Bronze",
    "NG": "Natural Gas", "OZ": "Ounce", "PC": "Piece", "PROG": "Programmable",
    "PT": "Pint", "PXP": "PEX x PEX", "QD": "Quick Disconnect", "QT": "Quart",
    "QTR": "Quarter", "RF": "Round Front", "RND": "Round", "SJ": "Slip Joint",
    "SPLT": "Split", "SQ": "Square", "STRT": "Straight",
    "SXM": "Slip x MIP", "SXS": "Slip x Slip", "SXSXS": "Slip x Slip x Slip",
    "TKOF": "Takeoff", "TRN": "Turn", "TWL": "Towel", "UPFLOW": "Upflow",
    "Z/PLT": "Zinc Plated", "MIP": "MIP", "SEER2": "SEER2", "UL181": "UL181",
    "PTFE": "PTFE", "SVC": "Service", "WT": "Weight",
}

PIPE_SIZES: tuple[str, ...] = (
    "1/2", "3/4", "1", "1-1/4", "1-1/2", "2", "2-1/2", "3", "4", "6",
)

CATEGORIES: tuple[Category, ...] = (
    Category(
        key="pvc_dwv",
        department="PLUMBING - PIPE & FITTINGS",
        noun="ELL", long_noun="Elbow",
        vendors=("CHARLOTTE PIPE", "SPEARS", "NIBCO", "GENOVA"),
        sizes=PIPE_SIZES,
        specs=("PVC S40 SXS 90", "PVC S40 SXS 45", "PVC DWV 90", "ABS DWV 60", "ABS DWV VENT 90"),
        unit_of_measure="EA", price_range=(0.85, 24.0),
    ),
    Category(
        key="pvc_fittings",
        department="PLUMBING - PIPE & FITTINGS",
        noun="", long_noun="Fitting",
        vendors=("CHARLOTTE PIPE", "SPEARS", "NIBCO", "FERNCO"),
        sizes=PIPE_SIZES,
        specs=("PVC S40 SXS COUP", "ABS DWV COUP", "PVC S40 SXM ADPT", "CI PVC COUP",
               "PVC DWV SAN TEE"),
        unit_of_measure="EA", price_range=(0.75, 38.0),
    ),
    Category(
        key="plastic_pipe",
        department="PLUMBING - PIPE & FITTINGS",
        noun="PIPE", long_noun="Pipe",
        vendors=("CHARLOTTE PIPE", "JM EAGLE", "GENOVA", "ADS"),
        sizes=("1-1/4X10 FT", "1-1/2X10 FT", "2X10 FT", "3X10 FT", "4X10 FT", "1-1/4X300"),
        specs=("PVC DWV S40 PE", "ABS PLUS S40 FOAM", "IPS SIDR9 HDPE", "PVC S40 BE"),
        unit_of_measure="EA", price_range=(6.5, 148.0),
    ),
    Category(
        key="copper_fittings",
        department="PLUMBING - PIPE & FITTINGS",
        noun="", long_noun="Fitting",
        vendors=("NIBCO", "MUELLER", "ELKHART", "VIEGA"),
        sizes=("1/2", "3/4", "1", "1-1/4", "1-1/2", "2"),
        specs=("WROT COP CXC 90 ELL", "WROT COP CXC 45 ELL", "WROT COP CXC TEE",
               "WROT COP CAP", "WROT COP CXC COUP"),
        unit_of_measure="EA", price_range=(0.9, 42.0),
    ),
    Category(
        key="copper_tube",
        department="PLUMBING - PIPE & FITTINGS",
        noun="", long_noun="Hard Copper Tube",
        vendors=("MUELLER", "CERRO", "CAMBRIDGE-LEE"),
        sizes=("1/2 X 10", "3/4 X 10", "1 X 10", "1/2 X 20", "3/4 X 20"),
        specs=("L HARD COP TUBE", "M HARD COP TUBE", "K HARD COP TUBE"),
        unit_of_measure="EA", price_range=(18.0, 210.0),
    ),
    Category(
        key="pex",
        department="PLUMBING - PIPE & FITTINGS",
        noun="", long_noun="PEX Fitting",
        vendors=("UPONOR", "VIEGA", "SIOUX CHIEF", "APOLLO", "SHARKBITE"),
        sizes=("1/2", "3/4", "1", "1/2X3/4", "3/4X1/2"),
        specs=("PLAS F2159 PEX 90 ELL", "PEX F1960 STUB OUT ELL", "PLAS F1807 PEX COUP",
               "PEX CRMP RNG", "PLAS F2159 PEX COUP"),
        unit_of_measure="EA", price_range=(0.35, 26.0),
    ),
    Category(
        key="steel_pipe",
        department="PLUMBING - PIPE & FITTINGS",
        noun="NIP", long_noun="Nipple",
        vendors=("WARD", "ANVIL", "MERIT BRASS"),
        sizes=("1/2X6", "3/4X6", "1X6", "1-1/2X6", "2X6", "1/2XCLOSE", "3/4XCLOSE"),
        specs=("BLK STL", "GALV STL", "BLK MI 150#", "GALV MI 150#"),
        unit_of_measure="EA", price_range=(1.2, 34.0),
    ),
    Category(
        key="bronze_valves",
        department="PLUMBING - VALVES",
        noun="VLV", long_noun="Valve",
        vendors=("NIBCO", "MILWAUKEE VALVE", "APOLLO", "WATTS", "LEGEND"),
        sizes=("1/2", "3/4", "1", "1-1/4", "1-1/2", "2", "2-1/2"),
        specs=("BRZ 125# THRD RS GATE", "BRZ 150# THRD RS UB GATE", "LF BRS FULL PORT BALL",
               "BRZ 125# THRD SWING CHK", "BRZ 150# THRD GBL"),
        unit_of_measure="EA", price_range=(12.0, 420.0),
    ),
    Category(
        key="supply_stops",
        department="PLUMBING - FIXTURE TRIM",
        noun="ANG STP", long_noun="Angle Stop",
        vendors=("BRASSCRAFT", "SIOUX CHIEF", "KEENEY", "WATTS"),
        sizes=("1/2 FIP X1/2", "1/2 COMP X3/8", "5/8 OD X3/8", "1/2 SWT X3/8"),
        specs=("QTR TRN", "LF QTR TRN", "MULTI TURN"),
        unit_of_measure="EA", price_range=(6.0, 38.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="exposed_trim",
        department="PLUMBING - FIXTURE TRIM",
        noun="ESC", long_noun="Escutcheon",
        vendors=("KEENEY", "SIOUX CHIEF", "JONES STEPHENS", "BRASSCRAFT"),
        sizes=("1/2 IPS", "3/4 IPS", "1-1/4 IPS", "1-1/2 IPS", "2 IPS"),
        specs=("SHALLOW", "DEEP", "FLANGED"),
        unit_of_measure="EA", price_range=(2.5, 18.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="lav_faucet",
        department="PLUMBING - FIXTURE TRIM",
        noun="LAV FCT", long_noun="Lavatory Faucet",
        vendors=("MOEN", "DELTA", "KOHLER", "AMERICAN STANDARD", "GERBER", "PFISTER"),
        sizes=("4 IN CTRSET", "8 IN WIDESPREAD", "SINGLE HOLE", "CENTERSET 2 HDL"),
        specs=("LF 1.2 GPM", "LF 1.5 GPM", "LF 0.5 GPM METERING"),
        unit_of_measure="EA", price_range=(58.0, 640.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="kitchen_faucet",
        department="PLUMBING - FIXTURE TRIM",
        noun="KIT FCT", long_noun="Kitchen Faucet",
        vendors=("MOEN", "DELTA", "KOHLER", "GROHE", "BLANCO"),
        sizes=("SINGLE HDL PULLDOWN", "SINGLE HDL PULLOUT", "TWO HDL BRIDGE", "BAR PREP"),
        specs=("LF 1.5 GPM", "LF 1.8 GPM", "LF 2.2 GPM"),
        unit_of_measure="EA", price_range=(120.0, 890.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="shower_trim",
        department="PLUMBING - FIXTURE TRIM",
        noun="SHWR TRIM", long_noun="Shower Trim",
        vendors=("MOEN", "DELTA", "KOHLER", "SYMMONS", "PFISTER"),
        sizes=("1 HDL", "2 HDL", "3 HDL", "TUB/SHWR 1 HDL"),
        specs=("PRESS BAL", "THERMO", "PRESS BAL W/ DIV"),
        unit_of_measure="EA", price_range=(78.0, 520.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="shower_head",
        department="PLUMBING - FIXTURE TRIM",
        noun="H/SHWR", long_noun="Hand Shower",
        vendors=("MOEN", "DELTA", "KOHLER", "SPEAKMAN"),
        sizes=("SINGLE FUNC", "MULTI FUNC", "6 FUNC W/ 60 HOSE"),
        specs=("1.75 GPM", "2.0 GPM", "2.5 GPM"),
        unit_of_measure="EA", price_range=(24.0, 210.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="bath_hardware",
        department="PLUMBING - BATH ACCESSORIES",
        noun="", long_noun="Bath Accessory",
        vendors=("MOEN", "DELTA", "GATCO", "BOBRICK"),
        sizes=("18 IN", "24 IN", "30 IN", "36 IN"),
        specs=("TWL BAR CONCEALED MNT", "TWL BAR EXPOSED MNT", "GRAB BAR 1-1/4",
               "TWL SHELF"),
        unit_of_measure="EA", price_range=(18.0, 145.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="china",
        department="PLUMBING - FIXTURES",
        noun="TOILET", long_noun="Toilet",
        vendors=("KOHLER", "AMERICAN STANDARD", "TOTO", "GERBER", "MANSFIELD"),
        sizes=("EL 1.28 GPF", "RF 1.6 GPF", "EL 1.6 GPF", "COMFORT HT EL"),
        specs=("2PC", "1PC", "2PC W/ SEAT"),
        unit_of_measure="EA", price_range=(180.0, 920.0),
        finishes=CHINA_COLOURS,
    ),
    Category(
        key="lavatory",
        department="PLUMBING - FIXTURES",
        noun="LAV", long_noun="Lavatory",
        vendors=("KOHLER", "AMERICAN STANDARD", "TOTO", "MANSFIELD"),
        sizes=("19X16", "20X17", "22X19", "24X20"),
        specs=("DROP IN", "UNDERMOUNT", "PEDESTAL", "WALL HUNG"),
        unit_of_measure="EA", price_range=(72.0, 480.0),
        finishes=CHINA_COLOURS,
    ),
    Category(
        key="toilet_seat",
        department="PLUMBING - FIXTURES",
        noun="TOILET SEAT", long_noun="Toilet Seat",
        vendors=("BEMIS", "CHURCH", "KOHLER", "AMERICAN STANDARD"),
        sizes=("EL", "RF", "EL CLOSED FRONT", "RF OPEN FRONT"),
        specs=("PLAS SLOW CLOSE", "WOOD", "HD COMMERCIAL"),
        unit_of_measure="EA", price_range=(14.0, 96.0),
        finishes=CHINA_COLOURS,
    ),
    Category(
        key="sink",
        department="PLUMBING - FIXTURES",
        noun="SINK", long_noun="Sink",
        vendors=("ELKAY", "MOEN", "BLANCO", "JUST MFG"),
        sizes=("25X22", "33X22", "23X18", "31X18"),
        specs=("18GA SINGLE BOWL", "16GA DOUBLE BOWL", "18GA UNDERMOUNT"),
        unit_of_measure="EA", price_range=(96.0, 620.0),
        finishes=SINK_FINISHES,
    ),
    Category(
        key="water_heater",
        department="PLUMBING - WATER HEATERS",
        noun="WHTR", long_noun="Water Heater",
        vendors=("RHEEM", "AO SMITH", "BRADFORD WHITE", "STATE", "NAVIEN"),
        sizes=("40 GAL", "50 GAL", "75 GAL", "199 KBTU", "160 KBTU"),
        specs=("NG ATMOS VENT", "LP ATMOS VENT", "ELEC 4500W", "NG TANKLESS", "HYBRID HP"),
        unit_of_measure="EA", price_range=(520.0, 3800.0),
    ),
    Category(
        key="pumps",
        department="PLUMBING - PUMPS",
        noun="PUMP", long_noun="Pump",
        vendors=("LIBERTY PUMPS", "ZOELLER", "TACO", "GRUNDFOS", "LITTLE GIANT"),
        sizes=("1/3 HP", "1/2 HP", "3/4 HP", "1 HP"),
        specs=("SUMP CAST IRON", "SEWAGE 2 IN", "EFFLUENT", "CIRC BRZ", "CONDENSATE"),
        unit_of_measure="EA", price_range=(78.0, 1450.0),
    ),
    Category(
        key="hangers",
        department="PLUMBING - HANGERS & SUPPORTS",
        noun="", long_noun="Hanger",
        vendors=("SIOUX CHIEF", "TOLCO", "ANVIL", "ERICO"),
        sizes=("1/2", "3/4", "1", "1-1/4", "1-1/2", "2"),
        specs=("Z/PLT SPLT RNG HGR 3/8 BT", "CLEVIS HGR", "STUD GUARD 16 GA", "STRUT CLMP"),
        unit_of_measure="EA", price_range=(0.45, 16.0),
    ),
    Category(
        key="chemicals",
        department="PLUMBING - CHEMICALS",
        noun="", long_noun="Solvent Cement",
        vendors=("OATEY", "RECTORSEAL", "HERCULES", "IPS"),
        sizes=("4 OZ", "8 OZ", "1 PT", "1 QT", "1 GAL"),
        specs=("ABS CMNT 773", "PVC REG BODY CMNT", "CPVC ORANGE CMNT", "PURPLE PRIMER",
               "THRD SEALANT"),
        unit_of_measure="EA", price_range=(4.5, 68.0),
    ),
    Category(
        key="waterworks",
        department="WATERWORKS - METERS & BRASS",
        noun="", long_noun="Waterworks Brass",
        vendors=("FORD METER BOX", "MUELLER", "AY MCDONALD", "BADGER METER"),
        sizes=("5/8", "3/4", "1", "1-1/2", "2"),
        specs=("LFN WTR MTR COUP", "LF BRS WTR MTR COUP", "IPS X METER COUP",
               "LF BRS CORP STOP"),
        unit_of_measure="EA", price_range=(14.0, 340.0),
    ),
    Category(
        key="backflow",
        department="WATERWORKS - BACKFLOW",
        noun="BACKFLOW PREV", long_noun="Backflow Preventer",
        vendors=("WATTS", "FEBCO", "ZURN WILKINS", "APOLLO"),
        sizes=("3/4", "1", "1-1/4", "1-1/2", "2"),
        specs=("LF RPZ", "LF DCVA", "LF PVB", "LF DUAL CHK"),
        unit_of_measure="EA", price_range=(96.0, 1250.0),
    ),
    Category(
        key="air_dist_grille",
        department="HVAC - AIR DIST",
        noun="RTN AIR GRL", long_noun="Return Air Grille",
        vendors=("PROSELECT", "HART & COOLEY", "SHOEMAKER", "TRUAIRE"),
        sizes=("10X10", "12X12", "14X14", "16X20", "20X20", "24X24", "30X6", "12X6"),
        specs=("RG 1/2 FIN", "STL 1-WAY", "ALUM 3-WAY", "STL W/ FLTR FRAME"),
        unit_of_measure="EA", price_range=(6.5, 128.0),
        finishes=GRILLE_FINISHES,
    ),
    Category(
        key="air_dist_register",
        department="HVAC - AIR DIST",
        noun="REG", long_noun="Register",
        vendors=("PROSELECT", "HART & COOLEY", "SHOEMAKER", "TRUAIRE"),
        sizes=("4X10", "4X12", "6X10", "6X12", "8X12", "10X6"),
        specs=("FLR STL 2-WAY", "S/WALL STL 3-WAY", "CEIL ALUM 4-WAY", "BASEBOARD"),
        unit_of_measure="EA", price_range=(4.2, 62.0),
        finishes=GRILLE_FINISHES,
    ),
    Category(
        key="flex_duct",
        department="HVAC - AIR DIST",
        noun="FLEX AIR DUCT", long_noun="Flexible Air Duct",
        vendors=("ATCO", "THERMAFLEX", "HART & COOLEY", "PROSELECT"),
        sizes=("6X25", "8X25", "10X25", "12X25", "14X25", "16X25", "20X25"),
        specs=("SILV R6.0", "SILV R8.0", "BLK R6.0", "UNFD"),
        unit_of_measure="EA", price_range=(28.0, 168.0),
    ),
    Category(
        key="duct_fittings",
        department="HVAC - AIR DIST",
        noun="", long_noun="Duct Fitting",
        vendors=("PROSELECT", "SNAPPY", "IMPERIAL MFG", "HART & COOLEY"),
        sizes=("4", "5", "6", "7", "8", "10", "12"),
        specs=("GALV SPIN-IN TKOF W/ DMPR", "GALV STRT BOOT", "GALV RND END BOOT",
               "GALV RND DMPR"),
        unit_of_measure="EA", price_range=(3.4, 58.0),
    ),
    Category(
        key="duct_insulation",
        department="HVAC - INSULATION",
        noun="DUCT WRAP", long_noun="Duct Wrap",
        vendors=("JOHNS MANVILLE", "OWENS CORNING", "KNAUF"),
        sizes=("1-1/2X48X100", "2X48X75", "1-1/4X12X150", "3X48X50"),
        specs=("FSK R6", "UNFD R4.2", "FSK R8"),
        unit_of_measure="EA", price_range=(38.0, 210.0),
    ),
    Category(
        key="air_filters",
        department="HVAC - FILTRATION",
        noun="PLEAT FLTR", long_noun="Pleated Filter",
        vendors=("PROSELECT", "FLANDERS", "AAF", "3M"),
        sizes=("16X20X1", "16X25X1", "20X20X1", "20X25X1", "20X25X4", "24X24X2"),
        specs=("KEYPLEAT M8", "MERV 8", "MERV 11", "MERV 13"),
        unit_of_measure="BX", price_range=(4.8, 96.0),
    ),
    Category(
        key="thermostats",
        department="HVAC - CONTROLS",
        noun="TSTAT", long_noun="Thermostat",
        vendors=("HONEYWELL", "PROSELECT", "EMERSON", "ECOBEE"),
        sizes=("1H/1C", "2H/1C", "3H/2C", "1H/1C HP"),
        specs=("PROG 7 DAY", "NON PROG", "WIFI SMART", "T4 PRO PROG"),
        unit_of_measure="EA", price_range=(28.0, 285.0),
    ),
    Category(
        key="condensing_units",
        department="HVAC - EQUIPMENT",
        noun="COND UNIT", long_noun="Condensing Unit",
        vendors=("CARRIER", "GOODMAN", "RHEEM", "LENNOX", "TRANE"),
        sizes=("18 KBTU", "24 KBTU", "36 KBTU", "48 KBTU", "60 KBTU"),
        specs=("R454B 14.3 SEER2", "R454B 15.2 SEER2", "R410A 14 SEER"),
        unit_of_measure="EA", price_range=(1180.0, 4900.0),
    ),
    Category(
        key="furnaces",
        department="HVAC - EQUIPMENT",
        noun="FURN", long_noun="Furnace",
        vendors=("CARRIER", "GOODMAN", "RHEEM", "LENNOX", "TRANE"),
        sizes=("60 MBH", "80 MBH", "100 MBH", "120 MBH"),
        specs=("80% UPFLOW", "96% UPFLOW 2 STG", "80% HORIZ", "96% DOWNFLOW"),
        unit_of_measure="EA", price_range=(880.0, 3400.0),
    ),
    Category(
        key="hvac_parts",
        department="HVAC - REPAIR PARTS",
        noun="MTR ASSY", long_noun="Motor Assembly",
        vendors=("PROSELECT", "FASCO", "GENTEQ", "MARS", "SUPCO"),
        sizes=("1/12 HP", "1/6 HP", "1/3 HP", "1/2 HP"),
        specs=("IND DRFT", "BLWR 208/230V", "CONDENSER FAN", "ECM DIRECT DRIVE"),
        unit_of_measure="EA", price_range=(42.0, 680.0),
    ),
    Category(
        key="refrigeration",
        department="HVAC - REFRIGERATION",
        noun="LINE SET", long_noun="Line Set",
        vendors=("MUELLER", "DIVERSITECH", "JB INDUSTRIES", "NATIONAL"),
        sizes=("1/4X1/2X25", "3/8X3/4X25", "3/8X7/8X50", "1/2X7/8X25"),
        specs=("INSUL", "UNINSUL", "INSUL W/ FITTINGS"),
        unit_of_measure="EA", price_range=(52.0, 340.0),
    ),
    Category(
        key="gas_train",
        department="PLUMBING - GAS",
        noun="GAS HOSE QD KIT", long_noun="Gas Hose Quick Disconnect Kit",
        vendors=("DORMONT", "BRASSCRAFT", "T&S BRASS"),
        sizes=("1/2 X 36", "3/4 X 48", "1 X 60", "1/2 X 24"),
        specs=("SS BRAIDED", "COATED SS", "SS W/ SWIVEL"),
        unit_of_measure="EA", price_range=(38.0, 260.0),
    ),
    Category(
        key="drains",
        # No finish. A floor drain's strainer material is already stated in the
        # spec -- `NB` is nickel bronze -- and a plated finish on a cast-iron
        # body set in a slab is not a thing this trade sells.
        department="PLUMBING - DRAINAGE",
        noun="FLR DRN", long_noun="Floor Drain",
        vendors=("ZURN", "JAY R SMITH", "SIOUX CHIEF", "WATTS"),
        sizes=("2", "3", "4", "6"),
        specs=("CI W/ NB STRN", "PVC W/ RND STRN", "ADJ W/ SQ STRN"),
        unit_of_measure="EA", price_range=(28.0, 420.0),
    ),
    Category(
        key="traps",
        # Concealed traps: plastic, cast iron, zinc slip nut. Nothing anybody
        # sees, so nothing anybody chooses a finish for.
        department="PLUMBING - DRAINAGE",
        noun="P TRAP", long_noun="P Trap",
        vendors=("KEENEY", "SIOUX CHIEF", "OATEY", "JONES STEPHENS"),
        sizes=("1-1/4", "1-1/2", "2"),
        specs=("PLAS SJ", "ZN SJ SLIP NUT", "CI SVC WT"),
        unit_of_measure="EA", price_range=(3.2, 78.0),
    ),
    Category(
        key="exposed_trap",
        # The exposed half of the same department: a brass trap under a wall
        # hung lavatory is chosen by look, so it does carry a finish.
        department="PLUMBING - FIXTURE TRIM",
        noun="P TRAP", long_noun="Exposed P Trap",
        vendors=("KEENEY", "MCGUIRE", "JONES STEPHENS", "BRASSCRAFT"),
        sizes=("1-1/4", "1-1/2", "1-1/4X1-1/2"),
        specs=("17GA BRS SJ", "20GA BRS SJ", "17GA BRS W/ CLNOUT"),
        unit_of_measure="EA", price_range=(14.0, 128.0),
        finishes=METAL_FINISHES,
    ),
    Category(
        key="disposals",
        department="PLUMBING - APPLIANCES",
        noun="DISPOSER", long_noun="Garbage Disposer",
        vendors=("INSINKERATOR", "WASTE KING", "MOEN"),
        sizes=("1/3 HP", "1/2 HP", "3/4 HP", "1 HP"),
        specs=("CONT FEED", "BATCH FEED", "CONT FEED W/ CORD"),
        unit_of_measure="EA", price_range=(88.0, 540.0),
    ),
    Category(
        key="expansion",
        department="PLUMBING - VALVES",
        noun="EXP TANK", long_noun="Expansion Tank",
        vendors=("AMTROL", "WATTS", "ZURN WILKINS", "TACO"),
        sizes=("2 GAL", "4.4 GAL", "10.3 GAL", "25 GAL"),
        specs=("LF THERM-X-TROL", "POTABLE", "HYDRONIC"),
        unit_of_measure="EA", price_range=(48.0, 380.0),
    ),
    Category(
        key="tape_and_sundries",
        department="PLUMBING - SUNDRIES",
        noun="TAPE", long_noun="Tape",
        vendors=("SHURTAPE", "OATEY", "3M", "NASHUA"),
        sizes=("2X60 YD", "1/2X520 IN", "3X50 YD"),
        specs=("FOIL UL181", "PTFE THRD SEAL", "CLOTH DUCT"),
        unit_of_measure="EA", price_range=(2.4, 26.0),
    ),
)

#: How a SKU is spelled. Four shapes, taken from the real 482.
SKU_SHAPES: tuple[str, ...] = (
    "letter_digits",   # Q1685, B443213
    "alpha_block",     # ADWVCFAMRPM, BRCGF
    "prefix_digits",   # PSRGW1212, AGCR4000L010000N
    "long_numeric",    # R7010108781
)


@dataclass(frozen=True)
class GeneratedProduct:
    """One catalogue entry, with everything a `lkpSearchProduct` document needs."""

    product_id: str
    sku: str
    description: str
    long_description: str
    web_display_name: str
    vendor: str
    department: str
    unit_of_measure: str
    unit_of_measure_description: str
    brand_type: str
    upc_code: str
    list_price: float
    colour_finish: str | None
    category_key: str
    #: True when every stated value came off a real order line. Real entries
    #: carry no vendor, department or finish: the line does not state them, and
    #: guessing against a real catalogue number would put a claim in the corpus
    #: that no source ever made.
    real: bool = False


UOM_DESCRIPTIONS: dict[str, str] = {
    "EA": "EACH",
    "BX": "BOX",
    "CS": "CASE",
    "FT": "FEET",
    "RL": "ROLL",
}


# fmt: on


def expand(abbreviated: str) -> str:
    """Turn an ERP description into words an associate would actually type.

    `16X25 SILV FLEX AIR DUCT R8.0` is unsearchable as written; the copilot
    matches free text against `webDisplayName`, so the expanded form is what
    makes "silver flexible air duct" find anything. A token with no expansion is
    title-cased rather than dropped -- sizes and model fragments carry meaning.
    """
    words: list[str] = []
    for token in abbreviated.split():
        expansion = ABBREVIATIONS.get(token)
        if expansion is not None:
            words.append(expansion)
        elif any(character.isdigit() for character in token):
            words.append(token)
        else:
            words.append(token.title())
    return " ".join(words)
