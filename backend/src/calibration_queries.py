"""Hand-authored calibration queries for the /test command.

Each query anchors a question to the specific fact_ids a correct, complete
answer should draw on -- this lets grading check actual fact coverage
(did the answer really reflect fact X) rather than just plausibility.

For "speculative" queries, required_fact_ids point at established
character-trait facts the answer should stay *consistent with*, not facts
it should quote -- there's no ground-truth answer to a hypothetical, so the
judge grades consistency with known traits instead of literal coverage.
"""

from typing import Any, Dict, List

CALIBRATION_QUERIES: List[Dict[str, Any]] = [
    {
        "query_id": "bio_kilgore",
        "category": "member_bio",
        "question": "Give me a biography of Alfredo Kilgore.",
        "required_fact_ids": [
            "20e295da-8896-48a9-99a9-8b4d375a4dfd",  # married Roxandra Ballbuster
            "0e649ece-c2e0-4b62-8d30-b00c1568fe19",  # adversarial relationship w/ Neville Haunt
            "2e79bfec-692f-42f2-8930-2cd801dbde49",  # Delta-Charon Harley 55
            "edd260b8-a306-42e6-8a94-6f3ec9020aeb",  # fattest member of Scuz Patrol
            "7c228313-8914-424b-830a-966b633f8f15",  # honeymoon trashed hotel planet Marriot-5
        ],
        "speculative": False,
    },
    {
        "query_id": "bio_kerosynth",
        "category": "member_bio",
        "question": "Give me a biography of KEROSYNTH.",
        "required_fact_ids": [
            "98f2e1ca-1f1d-4b64-ba86-10d4744f8540",  # murky origins / how she joined
            "50afcfec-bcb9-43ae-a622-7d9cde9cf8cb",  # toughest/scariest member
            "62c48ba7-19d9-4276-87b8-3a6b17d18c6a",  # 32-hour set, "Scuz Never Sleeps"
            "156dc48e-5af7-433d-a0a1-8bc944fca001",  # Gopnik Security Force / cloning vat
        ],
        "speculative": False,
    },
    {
        "query_id": "bio_neville",
        "category": "member_bio",
        "question": "Give me a biography of Neville Haunt.",
        "required_fact_ids": [
            "7ee0b4ee-ed9d-4b84-9e92-f69ad20f7404",  # numerous addictions
            "bb68f104-71d0-46ab-8465-5265f7b70fcb",  # 34 known bodies, 31 stolen
            "da0ea791-d7c6-4b24-ad9e-1b01ea0f8d34",  # smartest, misanthropy
            "7e172d1a-3375-4c48-8b22-bca672eb4c10",  # "non-standard humanoids" w/ Kilgore
        ],
        "speculative": False,
    },
    {
        "query_id": "band_chronology",
        "category": "chronology",
        "question": (
            "Walk me through the major events in Scuz Patrol's history in "
            "chronological order."
        ),
        "required_fact_ids": [
            "8168ce43-2f52-4b4f-9c52-c18eb9737226",  # 2055 Mars colonized by accident
            "3992e43a-5f41-4198-b1a9-650ef44b5fd9",  # 2089 Delta-Charon merger
            "b9ebec47-989e-4242-9d19-8f4284f539a2",  # 2147 Galactic Zoo Putsch
            "2707f793-e762-4a3f-9359-3c5990d3c7fd",  # 2142 DJ Casio standard
            "8e108c1a-1e71-469e-b7ca-1b4726c9ab83",  # 2155 MuziPro battle of the bands
            "bd570733-b62b-4e0d-9817-c36d73e49f55",  # 2160 Kleptus-Gamma incident
            "affeb1ba-1016-4679-b033-577589f0d176",  # 2157-58 Abject Anarchy tour
            "7fceff9a-4736-4a39-9b6b-526d3efe914e",  # 2172 Scuzfest
        ],
        "speculative": False,
    },
    {
        "query_id": "band_relationships",
        "category": "relationships",
        "question": "Describe the relationships between the members of Scuz Patrol.",
        "required_fact_ids": [
            "0e649ece-c2e0-4b62-8d30-b00c1568fe19",  # Kilgore/Haunt adversarial
            "7e172d1a-3375-4c48-8b22-bca672eb4c10",  # "non-standard humanoids" shared trait
            "50afcfec-bcb9-43ae-a622-7d9cde9cf8cb",  # Kilgore openly afraid of KEROSYNTH
        ],
        "speculative": False,
    },
    {
        "query_id": "discography",
        "category": "discography",
        "question": (
            "Tell me about Scuz Patrol's song discography -- what are some "
            "of their tracks and the stories behind them?"
        ),
        "required_fact_ids": [
            "3e20708b-2fdb-4e8d-ad45-cd1927fdbfb0",  # Stub Grinder
            "209d0afc-6288-48f4-87b7-19e27f4e635b",  # Loss Control
            "bcf1ea63-07e2-4106-ba07-c0e70127f341",  # King Captain / I.S.C. Excrutiator
            "0da67449-2228-4bff-924f-a97834275124",  # Delta-Charon Harley 55 (the song)
            "d10bd14f-da7d-495f-b3b1-39bfdbe451e7",  # Burn it Bitch / fan club cult
            "b20dde5e-b1af-4403-944c-a5815597fff0",  # Dissident
        ],
        "speculative": False,
    },
    {
        "query_id": "kilgore_solo",
        "category": "member_solo",
        "question": (
            "What are some memorable things Alfredo Kilgore has said or "
            "done on his own, outside of band activities?"
        ),
        "required_fact_ids": [
            "87cd6fd2-4dce-4741-a026-37d85cd991c7",
            "39a30d5d-e5eb-45e2-b5ed-aff7fe281a4e",
            "cce3546b-b861-4d8d-9513-b766447e0da3",
            "5ff96a89-ad4a-4499-a1da-1cda2921a392",
            "cc7366ac-4ac0-4136-9675-f5f364047711",
            "ea376a71-c00b-43fb-bc63-68813ac498a7",
        ],
        "speculative": False,
    },
    {
        "query_id": "minor_characters",
        "category": "minor_characters",
        "question": (
            "Who are some of the supporting/minor characters in the Scuz "
            "Patrol universe, and what's notable about them?"
        ),
        "required_fact_ids": [
            "455e8f15-f491-455f-bb73-df4b7192931a",  # Marcus Jury
            "117dbe19-4909-4577-99cc-f89d5203d6c4",  # Larynx Voxan / MuziPro Corp
            "fadb15c0-c60c-4cc5-811e-37ff7635ef3c",  # The Galactic Council
            "da043db9-f4b7-4297-97ad-805f585971da",  # S.P.A.M.U.S. / Captain Plexius Maxximus
            "9c41c360-1a4c-4cce-8fd6-9233bdc6bd57",  # The Scuzzies (fanbase)
        ],
        "speculative": False,
    },
    {
        "query_id": "collaborations",
        "category": "collaborations",
        "question": "What collaborations has Scuz Patrol done with other artists?",
        "required_fact_ids": [
            "a6ef28db-ec64-4c71-9ef3-ef96f850a964",  # Archipelago single, 2173
            "3f0298da-9656-4183-bd58-ae0ef70ee1a2",  # Archipelago release details
            "89dc4a33-70c3-41c3-bead-4d1d3a37f6c5",  # Admiral Wart collab supplemental
        ],
        "speculative": False,
    },
    {
        "query_id": "conflicts",
        "category": "conflicts",
        "question": "What conflicts or controversies has Scuz Patrol been involved in?",
        "required_fact_ids": [
            "156dc48e-5af7-433d-a0a1-8bc944fca001",  # Gopnik Security Force affair
            "bd570733-b62b-4e0d-9817-c36d73e49f55",  # Kleptus-Gamma incident
            "43c0d688-774c-490c-b3bf-748fa9c243de",  # electro-shock incident
            "7fceff9a-4736-4a39-9b6b-526d3efe914e",  # Scuzfest / burning the stage
        ],
        "speculative": False,
    },
    {
        "query_id": "speculative_neville_cash",
        "category": "speculative",
        "question": (
            "If Neville Haunt found a suitcase full of untraceable cash "
            "on tour, what would he most likely do with it, and why?"
        ),
        "required_fact_ids": [
            "da0ea791-d7c6-4b24-ad9e-1b01ea0f8d34",  # smart but self-interested/misanthropic
            "bb68f104-71d0-46ab-8465-5265f7b70fcb",  # reckless/impulsive (34 bodies, 31 stolen)
            "7ee0b4ee-ed9d-4b84-9e92-f69ad20f7404",  # substance habits
        ],
        "speculative": True,
    },
]
