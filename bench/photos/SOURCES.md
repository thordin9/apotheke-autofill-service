# Test photo sources

All 19 photos come from Wikimedia Commons: the 9 original single-product photos and, since 2026-10-18, 10 photos
with brand names, model numbers or serial numbers (second table). Each of the original 9 is the Commons 1280 px
thumbnail of the original, shrunk to 1024 px on the long side (JPEG quality 85, Pillow), which matches what Apotheke sends to the service.

| File | Commons page | Author | License | Expected category |
|---|---|---|---|---|
| cordless_drill.jpg | https://commons.wikimedia.org/wiki/File:CordlessDrill.jpg | Greg Hume | CC BY-SA 3.0 | Tools |
| desk_lamp.jpg | https://commons.wikimedia.org/wiki/File:Blaue_Halogen-Schreibtischlampe_links_2022.jpg | VSchagow | CC BY-SA 4.0 | Lighting |
| hammer.jpg | https://commons.wikimedia.org/wiki/File:Claw-hammer.jpg | Evan-Amos | Public domain | Tools |
| headphones.jpg | https://commons.wikimedia.org/wiki/File:Bose_QuietComfort_25_Acoustic_Noise_Cancelling_Headphones_with_Carry_Case.jpg | Florian Fuchs | CC BY-SA 3.0 | Electronics |
| kitchen_knife.jpg | https://commons.wikimedia.org/wiki/File:2020_No%C5%BCe_kuchenne_Gerlach.jpg | Jacek Halicki | CC BY-SA 4.0 | Kitchen |
| laptop.jpg | https://commons.wikimedia.org/wiki/File:IBM_Thinkpad_R51.jpg | André Karwath (Aka) | CC BY-SA 4.0 | Electronics |
| paperback_book.jpg | https://commons.wikimedia.org/wiki/File:Stripped_paperback.jpg | Packer1028 | CC0 | Books |
| sneakers.jpg | https://commons.wikimedia.org/wiki/File:Reebok_Royal_Glide_Ripple_Clip_shoe.jpg | Petar Milošević | CC BY-SA 4.0 | Shoes |
| t-shirt.jpg | https://commons.wikimedia.org/wiki/File:BlueIEditShirt-plainBackground.jpeg | Daniel Furon for the Wikimedia Foundation | CC BY-SA 3.0 | Clothing |

## Brand, model and serial photos (added 2026-10-18)

Each file is the Commons 1600 px thumbnail of the original, shrunk to 1024 px on the long side (JPEG quality 85,
Pillow). Expected categories use Apotheke's default category list for new installs (`General, Tools, Electronics,
Kitchen, Documents, Hobby, Clothing, Shoes, Books, Lighting`); medicine packs have no better fit than General.
Brand, model and serial are what a person can read in the 1024 px file. The machine-readable version, used by
[`bench/score.py`](../score.py), is [`ground_truth.json`](ground_truth.json).

| File | Commons page | Author | License | Expected category | Brand | Model / product | Serial / ID |
|---|---|---|---|---|---|---|---|
| sony_cd_player_label.jpg | https://commons.wikimedia.org/wiki/File:Sony_CDP-CE525_serial_number.jpg | Vogler | CC BY-SA 4.0 | Electronics | Sony | CDP-CE525 | 5104890 |
| panasonic_vcr_label.jpg | https://commons.wikimedia.org/wiki/File:Panasonic_NV-G12EO_serial_number.jpg | Vogler | CC BY-SA 4.0 | Electronics | Panasonic | NV-G12EO | E7KL06311 |
| makita_drill_dhp453.jpg | https://commons.wikimedia.org/wiki/File:Makita_dhp453.jpg | Pixeltest | CC BY-SA 4.0 | Tools | Makita | DHP453 | – |
| ibuprofen_al400_pack.jpg | https://commons.wikimedia.org/wiki/File:Ibuprofen-400.jpg | Wesalius | CC BY-SA 4.0 | General | Ibuprofen AL | AL 400 | – |
| raspberry_pi4.jpg | https://commons.wikimedia.org/wiki/File:Raspberry_Pi_4_Model_B_-_Top.jpg | Laserlicht | CC BY-SA 4.0 | Electronics | Raspberry Pi | Raspberry Pi 4 Model B | 2ABCB-RPI4B (FCC ID) |
| fluke45_multimeter.jpg | https://commons.wikimedia.org/wiki/File:Fluke_45_Digital_Multimeter.jpg | Whiteknight | CC BY-SA 4.0 | Electronics (Tools also accepted) | Fluke | 45 | – |
| casio_fx991ex.jpg | https://commons.wikimedia.org/wiki/File:Casio_fx-991EX_Scientific_Calculator.jpg | David Wheatley | CC0 | Electronics | Casio | fx-991EX | – |
| tefal_kettle.jpg | https://commons.wikimedia.org/wiki/File:Tefal_KO299-9251.jpg | Raimond Spekking | CC BY-SA 4.0 | Kitchen | Tefal | Express | – |
| panadol_500_pack.jpg | https://commons.wikimedia.org/wiki/File:Panadol_500_mg.jpg | JIP | CC BY-SA 4.0 | General | Panadol | 500 mg | 05 79 65 (Vnr, Nordic article number) |
| osram_lamp_box.jpg | https://commons.wikimedia.org/wiki/File:Pictograms_on_Osram_light_bulb_packaging.jpeg | Pittigrilli | CC BY-SA 4.0 | Lighting | OSRAM | – | – |


On 2026-10-18 the hammer, desk_lamp, t-shirt and cordless_drill photos were replaced with plain single-product
shots, because QA found the earlier ones were not the named products.

The photos are scaled copies of the originals; their licenses are those of the originals (CC BY-SA files stay
CC BY-SA, with the attribution above). They are not covered by the repository's MIT license.
