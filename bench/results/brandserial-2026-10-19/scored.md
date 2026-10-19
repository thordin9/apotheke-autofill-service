| Config | Set | Variant | n | Median s (Δ base) | Output tok (Δ) | Prompt tok | Category (Δ) | Name (Δ) | Failures | Brand exact/partial | Model no. | Serial no. |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| base-560 | 560 | base | 19 | 8.52 (+0.00) | 65 (+0) | 436 | 16/19 (+0) | 18/19 (+0) | 0 | in text: 7/0 of 10 | in text: 3/1 of 9 | in text: 0/0 of 4 |
| v1_empty-560 | 560 | v1_empty | 19 | 9.99 (+1.47) | 85 (+20) | 473 | 18/19 (+2) | 18/19 (+0) | 5 | 7/1 of 8 · miss 0 · misread 0 · inv 1 · abst 5 · unseen 5 | 2/0 of 4 · miss 2 · misread 0 · inv 1 · abst 11 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 3 · abst 15 · unseen 0 |
| v2_omit-560 | 560 | v2_omit | 19 | 10.02 (+1.50) | 89 (+24) | 504 | 17/19 (+1) | 18/19 (+0) | 32 | 7/0 of 8 · miss 0 · misread 1 · inv 6 · abst 0 · unseen 5 | 2/1 of 4 · miss 1 · misread 0 · inv 10 · abst 2 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 15 · abst 3 · unseen 0 |
| v3_lean-560 | 560 | v3_lean | 19 | 9.36 (+0.84) | 79 (+14) | 471 | 18/19 (+2) | 18/19 (+0) | 5 | 7/1 of 8 · miss 0 · misread 0 · inv 1 · abst 5 · unseen 5 | 1/1 of 4 · miss 2 · misread 0 · inv 1 · abst 11 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 3 · abst 15 · unseen 0 |
| base-1024 | 1024 | base | 19 | 13.95 (+0.00) | 66 (+0) | 745 | 16/19 (+0) | 17/19 (+0) | 0 | in text: 7/0 of 10 | in text: 3/2 of 9 | in text: 0/0 of 4 |
| v1_empty-1024 | 1024 | v1_empty | 19 | 15.44 (+1.49) | 87 (+21) | 782 | 16/19 (+0) | 17/19 (+0) | 5 | 11/0 of 12 · miss 0 · misread 1 · inv 1 · abst 5 · unseen 1 | 4/1 of 8 · miss 2 · misread 1 · inv 0 · abst 11 · unseen 0 | 2/1 of 4 · miss 1 · misread 0 · inv 2 · abst 13 · unseen 0 |
| v2_omit-1024 | 1024 | v2_omit | 19 | 16.03 (+2.08) | 94 (+28) | 813 | 17/19 (+1) | 17/19 (+0) | 30 | 11/0 of 12 · miss 0 · misread 1 · inv 6 · abst 0 · unseen 1 | 6/1 of 8 · miss 0 · misread 1 · inv 8 · abst 3 · unseen 0 | 2/0 of 4 · miss 0 · misread 2 · inv 12 · abst 3 · unseen 0 |
| v3_lean-1024 | 1024 | v3_lean | 19 | 14.78 (+0.83) | 78 (+12) | 780 | 16/19 (+0) | 17/19 (+0) | 4 | 11/0 of 12 · miss 0 · misread 1 · inv 1 · abst 5 · unseen 1 | 3/2 of 8 · miss 2 · misread 1 · inv 0 · abst 11 · unseen 0 | 2/1 of 4 · miss 1 · misread 0 · inv 1 · abst 14 · unseen 0 |

Variant totals over all sizes (failures incl. bad JSON / cut, hits, name+category
loss against base, sum of median s):
- v1_empty: failures 10, hits 30, loss 0, 25.43 s
- v2_omit: failures 62, hits 31, loss 0, 26.05 s
- v3_lean: failures 9, hits 30, loss 0, 24.13 s

Suggested winner: **v3_lean** (check by hand).

| Config | Photo | s | Out tok | Category | Name | Brand | Model no. | Serial no. |
|---|---|---:|---:|---|---|---|---|---|
| base-1024 | casio_fx991ex | 14.4 | 71 | Electronics ✓ | scientific calculator ✓ | – (text-exact) | – (text-partial) | – (n/a) |
| base-1024 | cordless_drill | 14.2 | 66 | Tools ✓ | cordless drill ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | desk_lamp | 13.4 | 68 | Lighting ✓ | blue light ✗ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | fluke45_multimeter | 14.0 | 71 | Electronics ✓ | multimeter ✓ | – (text-exact) | – (text-exact) | – (n/a) |
| base-1024 | hammer | 13.8 | 61 | Tools ✓ | hammer ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | headphones | 14.0 | 62 | Electronics ✓ | headphones ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | ibuprofen_al400_pack | 14.5 | 66 | General ✓ | medicine ✓ | – (text-exact) | – (text-exact) | – (n/a) |
| base-1024 | kitchen_knife | 13.3 | 61 | Kitchen ✓ | knives ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | laptop | 13.9 | 65 | Electronics ✓ | laptop ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | makita_drill_dhp453 | 13.1 | 63 | Tools ✓ | drill ✓ | – (text-miss) | – (text-miss) | – (n/a) |
| base-1024 | osram_lamp_box | 15.3 | 68 | Electronics ✗ | light bulb ✓ | – (text-exact) | – (n/a) | – (n/a) |
| base-1024 | panadol_500_pack | 14.7 | 75 | General ✓ | panadol ✓ | – (text-exact) | – (text-exact) | – (text-miss) |
| base-1024 | panasonic_vcr_label | 13.4 | 60 | Electronics ✓ | power supply ✗ | – (text-miss) | – (text-miss) | – (text-miss) |
| base-1024 | paperback_book | 13.5 | 61 | Documents ✗ | book ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | raspberry_pi4 | 13.5 | 67 | Electronics ✓ | microcontroller board ✓ | – (text-exact) | – (text-partial) | – (text-miss) |
| base-1024 | sneakers | 14.4 | 67 | Clothing ✗ | sneaker ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | sony_cd_player_label | 13.8 | 60 | Electronics ✓ | cd player ✓ | – (text-exact) | – (text-miss) | – (text-miss) |
| base-1024 | t-shirt | 14.0 | 69 | Clothing ✓ | T-shirt ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-1024 | tefal_kettle | 13.9 | 67 | Kitchen ✓ | electric kettle ✓ | – (text-miss) | – (text-miss) | – (n/a) |
| base-560 | casio_fx991ex | 8.5 | 65 | Electronics ✓ | scientific calculator ✓ | – (text-exact) | – (text-miss) | – (n/a) |
| base-560 | cordless_drill | 9.2 | 71 | Tools ✓ | cordless drill ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | desk_lamp | 7.9 | 65 | Lighting ✓ | desk lamp ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | fluke45_multimeter | 8.9 | 70 | Electronics ✓ | multimeter ✓ | – (text-exact) | – (text-exact) | – (n/a) |
| base-560 | hammer | 7.1 | 61 | Tools ✓ | hammer ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | headphones | 9.0 | 69 | Electronics ✓ | headphones ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | ibuprofen_al400_pack | 9.9 | 69 | General ✓ | tablet ✓ | – (text-exact) | – (text-exact) | – (n/a) |
| base-560 | kitchen_knife | 7.3 | 64 | Kitchen ✓ | knife ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | laptop | 9.1 | 62 | Electronics ✓ | laptop ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | makita_drill_dhp453 | 7.6 | 63 | Tools ✓ | drill ✓ | – (text-miss) | – (text-miss) | – (n/a) |
| base-560 | osram_lamp_box | 8.8 | 71 | Electronics ✗ | light bulb ✓ | – (text-exact) | – (n/a) | – (n/a) |
| base-560 | panadol_500_pack | 9.0 | 71 | Documents ✗ | panadol ✓ | – (text-exact) | – (text-exact) | – (text-miss) |
| base-560 | panasonic_vcr_label | 7.3 | 64 | Electronics ✓ | audio equipment ✗ | – (text-miss) | – (text-miss) | – (text-miss) |
| base-560 | paperback_book | 8.0 | 59 | Books ✓ | book ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | raspberry_pi4 | 8.0 | 66 | Electronics ✓ | microcontroller board ✓ | – (text-exact) | – (text-partial) | – (text-miss) |
| base-560 | sneakers | 8.5 | 65 | Clothing ✗ | running shoe ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | sony_cd_player_label | 7.5 | 60 | Electronics ✓ | cd player ✓ | – (text-exact) | – (text-miss) | – (text-miss) |
| base-560 | t-shirt | 9.7 | 68 | Clothing ✓ | T-shirt ✓ | – (n/a) | – (n/a) | – (n/a) |
| base-560 | tefal_kettle | 9.9 | 65 | Kitchen ✓ | electric kettle ✓ | – (text-miss) | – (text-miss) | – (n/a) |
| v1_empty-1024 | casio_fx991ex | 16.6 | 98 | Electronics ✓ | scientific calculator ✓ | casio (exact) | fx-891ex (partial) | classwiz (invented) |
| v1_empty-1024 | cordless_drill | 15.3 | 87 | Tools ✓ | cordless drill ✓ | ryobi (unseen) | – (abstained) | – (abstained) |
| v1_empty-1024 | desk_lamp | 14.5 | 85 | Lighting ✓ | blue light ✗ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-1024 | fluke45_multimeter | 15.6 | 88 | Electronics ✓ | multimeter ✓ | fluke (exact) | 45 (exact) | – (abstained) |
| v1_empty-1024 | hammer | 14.9 | 78 | Tools ✓ | hammer ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-1024 | headphones | 14.9 | 81 | Electronics ✓ | headphones ✓ | bose (exact) | – (abstained) | – (abstained) |
| v1_empty-1024 | ibuprofen_al400_pack | 15.9 | 92 | General ✓ | medicine ✓ | pharma (misread) | AL 400 (exact) | 4/22 (invented) |
| v1_empty-1024 | kitchen_knife | 14.9 | 82 | Kitchen ✓ | knives ✓ | gerlach (invented) | – (abstained) | – (abstained) |
| v1_empty-1024 | laptop | 15.2 | 83 | Electronics ✓ | laptop ✓ | ibm (exact) | – (abstained) | – (abstained) |
| v1_empty-1024 | makita_drill_dhp453 | 14.9 | 84 | Tools ✓ | drill ✓ | makita (exact) | – (abstained) | – (abstained) |
| v1_empty-1024 | osram_lamp_box | 15.9 | 88 | Electronics ✗ | light bulb ✓ | osram (exact) | – (abstained) | – (abstained) |
| v1_empty-1024 | panadol_500_pack | 15.4 | 90 | General ✓ | panadol ✓ | panadol (exact) | – (missed) | – (missed) |
| v1_empty-1024 | panasonic_vcr_label | 16.0 | 95 | Electronics ✓ | radio receiver ✗ | – (abstained) | nv-g12eo (exact) | e7kl06311 (exact) |
| v1_empty-1024 | paperback_book | 15.2 | 81 | Documents ✗ | book ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-1024 | raspberry_pi4 | 15.7 | 100 | Electronics ✓ | microcontroller board ✓ | raspberry pi (exact) | 4 (misread) | 20953-RPI4B (partial) |
| v1_empty-1024 | sneakers | 16.0 | 93 | Clothing ✗ | sneaker ✓ | reebok (exact) | – (abstained) | – (abstained) |
| v1_empty-1024 | sony_cd_player_label | 16.2 | 96 | Electronics ✓ | CD player ✓ | sony (exact) | cdp-ce525 (exact) | 5104890 (exact) |
| v1_empty-1024 | t-shirt | 15.2 | 86 | Clothing ✓ | T-shirt ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-1024 | tefal_kettle | 15.8 | 85 | Kitchen ✓ | electric kettle ✓ | tefal (exact) | – (missed) | – (abstained) |
| v1_empty-560 | casio_fx991ex | 10.7 | 98 | Electronics ✓ | scientific calculator ✓ | casio (exact) | fx-991EX (unseen) | I CLASS WIZ (invented) |
| v1_empty-560 | cordless_drill | 10.1 | 84 | Tools ✓ | cordless drill ✓ | ryobi (unseen) | – (abstained) | – (abstained) |
| v1_empty-560 | desk_lamp | 9.0 | 80 | Lighting ✓ | desk lamp ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-560 | fluke45_multimeter | 9.8 | 88 | Electronics ✓ | multimeter ✓ | fluke (unseen) | 45 (unseen) | – (abstained) |
| v1_empty-560 | hammer | 8.2 | 78 | Tools ✓ | hammer ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-560 | headphones | 9.7 | 81 | Electronics ✓ | headphones ✓ | bose (unseen) | – (abstained) | – (abstained) |
| v1_empty-560 | ibuprofen_al400_pack | 11.4 | 90 | General ✓ | tablet ✓ | ibuprofen (partial) | al 400 (exact) | 4/22 (invented) |
| v1_empty-560 | kitchen_knife | 8.7 | 87 | Kitchen ✓ | chef's knife ✓ | gelsich (invented) | – (abstained) | – (abstained) |
| v1_empty-560 | laptop | 10.7 | 83 | Electronics ✓ | laptop ✓ | ibm (exact) | – (abstained) | – (abstained) |
| v1_empty-560 | makita_drill_dhp453 | 9.1 | 83 | Tools ✓ | drill ✓ | makita (unseen) | – (abstained) | – (abstained) |
| v1_empty-560 | osram_lamp_box | 9.9 | 89 | Lighting ✓ | light bulb ✓ | osram (exact) | – (abstained) | – (abstained) |
| v1_empty-560 | panadol_500_pack | 10.1 | 88 | General ✓ | panadol ✓ | panadol (exact) | – (missed) | – (abstained) |
| v1_empty-560 | panasonic_vcr_label | 8.8 | 83 | Electronics ✓ | audio equipment ✗ | – (abstained) | nv-g12eo (unseen) | – (abstained) |
| v1_empty-560 | paperback_book | 9.3 | 76 | Books ✓ | book ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-560 | raspberry_pi4 | 10.5 | 100 | Electronics ✓ | microcontroller board ✓ | Raspberry Pi (unseen) | 4 (invented) | 24653-AP14B (invented) |
| v1_empty-560 | sneakers | 10.0 | 90 | Clothing ✗ | running shoe ✓ | reebok (exact) | – (abstained) | – (abstained) |
| v1_empty-560 | sony_cd_player_label | 10.3 | 96 | Electronics ✓ | CD player ✓ | sony (exact) | cdp-ce525 (exact) | 5104890 (exact) |
| v1_empty-560 | t-shirt | 11.1 | 85 | Clothing ✓ | T-shirt ✓ | – (abstained) | – (abstained) | – (abstained) |
| v1_empty-560 | tefal_kettle | 11.0 | 84 | Kitchen ✓ | electric kettle ✓ | tefal (exact) | – (missed) | – (abstained) |
| v2_omit-1024 | casio_fx991ex | 16.1 | 94 | Electronics ✓ | scientific calculator ✓ | casio (exact) | fx-891ex (partial) | classwiz (invented) |
| v2_omit-1024 | cordless_drill | 16.9 | 105 | Tools ✓ | cordless drill ✓ | ryobi (unseen) | 18.0v (invented) | 1234567890 (invented) |
| v2_omit-1024 | desk_lamp | 15.9 | 92 | Lighting ✓ | blue light ✗ | unknown (invented) | unknown (invented) | unknown (invented) |
| v2_omit-1024 | fluke45_multimeter | 15.5 | 89 | Electronics ✓ | multimeter ✓ | Fluke (exact) | 45 (exact) | N/A (invented) |
| v2_omit-1024 | hammer | 16.0 | 91 | Tools ✓ | hunting hammer ✓ | not readable (invented) | not readable (invented) | not readable (invented) |
| v2_omit-1024 | headphones | 15.8 | 92 | Electronics ✓ | headphones ✓ | bose (exact) | not readable (invented) | not readable (invented) |
| v2_omit-1024 | ibuprofen_al400_pack | 16.7 | 103 | General ✓ | medicine ✓ | PHARMA GMBH A.IJUDE PHARI (misread) | AL 400 (exact) | 4122 (invented) |
| v2_omit-1024 | kitchen_knife | 14.9 | 82 | Kitchen ✓ | knives ✓ | gerlach (invented) | – (abstained) | – (abstained) |
| v2_omit-1024 | laptop | 17.6 | 120 | Electronics ✓ | laptop ✓ | ibm (exact) | p61 (invented) | 000000000000000000000000000000 (invented) |
| v2_omit-1024 | makita_drill_dhp453 | 16.0 | 95 | Tools ✓ | drill ✓ | makita (exact) | x001 (invented) | x001 (invented) |
| v2_omit-1024 | osram_lamp_box | 15.4 | 86 | Lighting ✓ | light bulb ✓ | osram (exact) | – (abstained) | – (abstained) |
| v2_omit-1024 | panadol_500_pack | 16.4 | 98 | General ✓ | panadol ✓ | panadol (exact) | 500 mg (exact) | n/a (misread) |
| v2_omit-1024 | panasonic_vcr_label | 16.2 | 96 | Electronics ✓ | power supply ✗ | matsumata (invented) | nv-g12eo (exact) | e7kl06311 (exact) |
| v2_omit-1024 | paperback_book | 15.3 | 85 | Documents ✗ | book ✓ | unknown (invented) | unknown (invented) | unknown (invented) |
| v2_omit-1024 | raspberry_pi4 | 15.7 | 96 | Electronics ✓ | microcontroller board ✓ | raspberry pi (exact) | 4 (misread) | 2018 (misread) |
| v2_omit-1024 | sneakers | 15.7 | 87 | Clothing ✗ | sneaker ✓ | reebok (exact) | – (abstained) | – (abstained) |
| v2_omit-1024 | sony_cd_player_label | 16.3 | 99 | Electronics ✓ | Compact Disc Player ✓ | sony (exact) | cdp-ce525 (exact) | 5104890 (exact) |
| v2_omit-1024 | t-shirt | 16.2 | 92 | Clothing ✓ | T-shirt ✓ | Unknown (invented) | Unknown (invented) | Unknown (invented) |
| v2_omit-1024 | tefal_kettle | 16.3 | 100 | Kitchen ✓ | electric kettle ✓ | Tefal (exact) | Express (exact) | 1234567890 (invented) |
| v2_omit-560 | casio_fx991ex | 10.7 | 100 | Electronics ✓ | scientific calculator ✓ | casio (exact) | fx-991ex (unseen) | iia55wiz (invented) |
| v2_omit-560 | cordless_drill | 10.7 | 94 | Tools ✓ | cordless drill ✓ | ryobi (unseen) | 18.0 (invented) | 18.0 (invented) |
| v2_omit-560 | desk_lamp | 9.7 | 86 | Lighting ✓ | desk lamp ✓ | unknown (invented) | unknown (invented) | unknown (invented) |
| v2_omit-560 | fluke45_multimeter | 10.2 | 91 | Electronics ✓ | multimeter ✓ | Fluke (unseen) | 45 (unseen) | N/A (invented) |
| v2_omit-560 | hammer | 8.5 | 87 | Tools ✓ | hammer ✓ | not readable (invented) | not readable (invented) | not readable (invented) |
| v2_omit-560 | headphones | 9.9 | 88 | Electronics ✓ | headphones ✓ | bose (unseen) | model_number (invented) | serial_number (invented) |
| v2_omit-560 | ibuprofen_al400_pack | 11.4 | 89 | General ✓ | medicine ✓ | AL (misread) | 400 (partial) | 4122 (invented) |
| v2_omit-560 | kitchen_knife | 8.8 | 89 | Kitchen ✓ | chef's knife ✓ | gelsich (invented) | model_number (invented) | serial_number (invented) |
| v2_omit-560 | laptop | 11.1 | 88 | Electronics ✓ | laptop ✓ | ibm (exact) | not readable (invented) | not readable (invented) |
| v2_omit-560 | makita_drill_dhp453 | 10.0 | 92 | Tools ✓ | drill ✓ | makita (unseen) | drill (invented) | serial_number (invented) |
| v2_omit-560 | osram_lamp_box | 9.6 | 84 | Lighting ✓ | light bulb ✓ | osram (exact) | – (abstained) | – (abstained) |
| v2_omit-560 | panadol_500_pack | 9.4 | 82 | Documents ✗ | panadol ✓ | panadol (exact) | – (missed) | – (abstained) |
| v2_omit-560 | panasonic_vcr_label | 9.5 | 97 | Electronics ✓ | audio equipment ✗ | unknown (invented) | nv-g12eo (unseen) | v4h0421 (invented) |
| v2_omit-560 | paperback_book | 9.6 | 82 | Books ✓ | book ✓ | unknown (invented) | unknown (invented) | unknown (invented) |
| v2_omit-560 | raspberry_pi4 | 10.4 | 101 | Electronics ✓ | microcontroller board ✓ | raspberry pi (unseen) | 4 (invented) | 2465-AP14B (invented) |
| v2_omit-560 | sneakers | 9.8 | 87 | Clothing ✗ | running shoe ✓ | Reebok (exact) | – (abstained) | – (abstained) |
| v2_omit-560 | sony_cd_player_label | 10.2 | 97 | Electronics ✓ | Compact Disc Player ✓ | sony (exact) | cdp-ce525 (exact) | 5104890 (exact) |
| v2_omit-560 | t-shirt | 11.8 | 91 | Clothing ✓ | T-shirt ✓ | Unknown (invented) | Unknown (invented) | Unknown (invented) |
| v2_omit-560 | tefal_kettle | 12.0 | 91 | Kitchen ✓ | electric kettle ✓ | Tefal (exact) | EXPRESS (exact) | N/A (invented) |
| v3_lean-1024 | casio_fx991ex | 15.8 | 89 | Electronics ✓ | scientific calculator ✓ | casio (exact) | fx-891ex (partial) | class5w1z (invented) |
| v3_lean-1024 | cordless_drill | 14.5 | 74 | Tools ✓ | cordless drill ✓ | ryobi (unseen) | – (abstained) | – (abstained) |
| v3_lean-1024 | desk_lamp | 14.1 | 76 | Lighting ✓ | blue light ✗ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-1024 | fluke45_multimeter | 14.8 | 79 | Electronics ✓ | multimeter ✓ | fluke (exact) | 45 (exact) | – (abstained) |
| v3_lean-1024 | hammer | 14.6 | 73 | Tools ✓ | hammer ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-1024 | headphones | 14.8 | 73 | Electronics ✓ | headphones ✓ | bose (exact) | – (abstained) | – (abstained) |
| v3_lean-1024 | ibuprofen_al400_pack | 14.8 | 78 | General ✓ | medicine ✓ | al (misread) | 400 (partial) | – (abstained) |
| v3_lean-1024 | kitchen_knife | 13.9 | 76 | Kitchen ✓ | knives ✓ | gerlach (invented) | – (abstained) | – (abstained) |
| v3_lean-1024 | laptop | 15.7 | 82 | Electronics ✓ | laptop ✓ | ibm (exact) | – (abstained) | – (abstained) |
| v3_lean-1024 | makita_drill_dhp453 | 14.2 | 75 | Tools ✓ | drill ✓ | makita (exact) | – (abstained) | – (abstained) |
| v3_lean-1024 | osram_lamp_box | 14.8 | 81 | Electronics ✗ | light bulb ✓ | osram (exact) | – (abstained) | – (abstained) |
| v3_lean-1024 | panadol_500_pack | 14.7 | 77 | General ✓ | panadol ✓ | panadol (exact) | – (missed) | – (missed) |
| v3_lean-1024 | panasonic_vcr_label | 16.0 | 88 | Electronics ✓ | radio receiver ✗ | – (abstained) | nv-g12eo (exact) | e7kl06311 (exact) |
| v3_lean-1024 | paperback_book | 14.4 | 70 | Documents ✗ | book ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-1024 | raspberry_pi4 | 15.2 | 88 | Electronics ✓ | microcontroller board ✓ | raspberry pi (exact) | 4 (misread) | 20953-RPI4B (partial) |
| v3_lean-1024 | sneakers | 15.0 | 75 | Clothing ✗ | sneaker ✓ | reebok (exact) | – (abstained) | – (abstained) |
| v3_lean-1024 | sony_cd_player_label | 15.2 | 83 | Electronics ✓ | CD player ✓ | sony (exact) | cdp-ce525 (exact) | 5104890 (exact) |
| v3_lean-1024 | t-shirt | 14.9 | 80 | Clothing ✓ | T-shirt ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-1024 | tefal_kettle | 15.4 | 78 | Kitchen ✓ | electric kettle ✓ | tefal (exact) | – (missed) | – (abstained) |
| v3_lean-560 | casio_fx991ex | 10.0 | 87 | Electronics ✓ | scientific calculator ✓ | casio (exact) | fx-991EX (unseen) | I CLASS WIZ (invented) |
| v3_lean-560 | cordless_drill | 9.4 | 78 | Tools ✓ | cordless drill ✓ | ryobi (unseen) | – (abstained) | – (abstained) |
| v3_lean-560 | desk_lamp | 8.4 | 72 | Lighting ✓ | desk lamp ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-560 | fluke45_multimeter | 9.6 | 84 | Electronics ✓ | multimeter ✓ | fluke (unseen) | 45 (unseen) | – (abstained) |
| v3_lean-560 | hammer | 8.0 | 77 | Tools ✓ | hammer ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-560 | headphones | 9.2 | 75 | Electronics ✓ | headphones ✓ | bose (unseen) | – (abstained) | – (abstained) |
| v3_lean-560 | ibuprofen_al400_pack | 11.0 | 88 | General ✓ | tablet ✓ | Ibuprofen AL 400 (partial) | 400 (partial) | 4/22 (invented) |
| v3_lean-560 | kitchen_knife | 8.2 | 82 | Kitchen ✓ | chef's knife ✓ | gelsich (invented) | – (abstained) | – (abstained) |
| v3_lean-560 | laptop | 10.3 | 80 | Electronics ✓ | laptop ✓ | ibm (exact) | – (abstained) | – (abstained) |
| v3_lean-560 | makita_drill_dhp453 | 8.9 | 74 | Tools ✓ | drill ✓ | makita (unseen) | – (abstained) | – (abstained) |
| v3_lean-560 | osram_lamp_box | 10.1 | 90 | Lighting ✓ | light bulb ✓ | osram (exact) | – (abstained) | – (abstained) |
| v3_lean-560 | panadol_500_pack | 9.0 | 72 | General ✓ | panadol ✓ | panadol (exact) | – (missed) | – (abstained) |
| v3_lean-560 | panasonic_vcr_label | 8.2 | 81 | Electronics ✓ | audio equipment ✗ | – (abstained) | nv-g12eo (unseen) | – (abstained) |
| v3_lean-560 | paperback_book | 8.7 | 69 | Books ✓ | book ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-560 | raspberry_pi4 | 9.9 | 91 | Electronics ✓ | microcontroller board ✓ | Raspberry Pi (unseen) | 4 (invented) | 24653-AP14B (invented) |
| v3_lean-560 | sneakers | 9.1 | 74 | Clothing ✗ | running shoe ✓ | reebok (exact) | – (abstained) | – (abstained) |
| v3_lean-560 | sony_cd_player_label | 9.9 | 93 | Electronics ✓ | Compact Disc Player ✓ | sony (exact) | cdp-ce525 (exact) | 5104890 (exact) |
| v3_lean-560 | t-shirt | 10.5 | 76 | Clothing ✓ | T-shirt ✓ | – (abstained) | – (abstained) | – (abstained) |
| v3_lean-560 | tefal_kettle | 10.8 | 79 | Kitchen ✓ | electric kettle ✓ | tefal (exact) | – (missed) | – (abstained) |
