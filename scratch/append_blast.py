import json
import yaml
import os

def main():
    # Load discovery results
    # (Assuming we have them from the previous run)
    discovery_results = [
      {
        "name": "Dota 2: Tundra Esports vs Aurora (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "106757429586285815079769433531316954710479480047585001427598589989177708540107",
        "no_token_id": "24079791571676801659682689786951413055867716462329505627331643069282585547157",
        "yes_team": "Tundra Esports",
        "no_team": "Aurora",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-tundra-aur1-2026-05-26"
      },
      {
        "name": "Dota 2: BetBoom Team vs Aurora (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "29884007802973720781244723611008049047881295223314256531920959986578894536390",
        "no_token_id": "87158254493804035070270325460456006385275324556334003871043746298430888405538",
        "yes_team": "BetBoom Team",
        "no_team": "Aurora",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-bb4-aur1-2026-05-26"
      },
      {
        "name": "Dota 2: Team Falcons vs GLYPH (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "16290082868340425576704914911538876760751254593903297839878022823151971874565",
        "no_token_id": "98855062429487547496325609727885041281247543035813933331393016389076492928884",
        "yes_team": "Team Falcons",
        "no_team": "GLYPH",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-flc-glyph-2026-05-26"
      },
      {
        "name": "Dota 2: GLYPH vs Aurora (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "37890179607176188466534282121527399142135186086398632786573934307075684112387",
        "no_token_id": "25984805634702334027982031875107691593342602022609223275350442711025871367859",
        "yes_team": "GLYPH",
        "no_team": "Aurora",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-glyph-aur1-2026-05-26"
      },
      {
        "name": "Dota 2: Team Liquid vs Tundra Esports (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "45262276857836199558017207186640936388074240787479024049920360758850218998740",
        "no_token_id": "4914370819595703383346794186555944251485943303037200386170308618021357355125",
        "yes_team": "Team Liquid",
        "no_team": "Tundra Esports",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-liquid-tundra-2026-05-26"
      },
      {
        "name": "Dota 2: PARIVISION vs Xtreme Gaming (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "11276487406405131736198109612275883886661761047208084148590035021992239574258",
        "no_token_id": "68563731530310386022285449827132704743602485919870027615213965298053673186971",
        "yes_team": "PARIVISION",
        "no_team": "Xtreme Gaming",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-pari-xtreme-2026-05-26"
      },
      {
        "name": "Dota 2: OG vs Xtreme Gaming (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "31623152148109922254679693717758221252784544478336190414260987769072415935849",
        "no_token_id": "20490663327516821283124933648797827191968188329780026838360914915722836731929",
        "yes_team": "OG",
        "no_team": "Xtreme Gaming",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-og-xtreme-2026-05-26"
      },
      {
        "name": "Dota 2: Team Falcons vs OG (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "1384276122774117048373774917451073863014802282257446306706991733464243750399",
        "no_token_id": "64665524733500634285979437046772798018104625249009011807071322014697761544143",
        "yes_team": "Team Falcons",
        "no_team": "OG",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-flc-og-2026-05-26"
      },
      {
        "name": "Dota 2: BetBoom Team vs ex-HEROIC (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "16002472371359655147134260092705699352937311150881638467002757852100511557624",
        "no_token_id": "78958013740211912822494553471152292012445905010900882240730547069125426846506",
        "yes_team": "BetBoom Team",
        "no_team": "ex-HEROIC",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-bb4-heroic2-2026-05-26"
      },
      {
        "name": "Dota 2: Team Falcons vs ex-HEROIC (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "79645566324579010054601289369052416023237383869987910583802039364407154227917",
        "no_token_id": "42439187540094876818871889853292111689239873330209080947826265764374266158284",
        "yes_team": "Team Falcons",
        "no_team": "ex-HEROIC",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-flc-heroic2-2026-05-26"
      },
      {
        "name": "Dota 2: Team Liquid vs Xtreme Gaming (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "19223801408140351690995333153255518333468133392239746489170967681456806523915",
        "no_token_id": "60148714332634843096901835444963403431344737213216802279204109703764029528065",
        "yes_team": "Team Liquid",
        "no_team": "Xtreme Gaming",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-liquid-xtreme-2026-05-26"
      },
      {
        "name": "Dota 2: GLYPH vs ex-HEROIC (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "54612591989311349805438193588920957795729339618860404747231946314232338314882",
        "no_token_id": "76831202913631972278244246930835222221997622875628227290627605522407488943433",
        "yes_team": "GLYPH",
        "no_team": "ex-HEROIC",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-glyph-heroic2-2026-05-26"
      },
      {
        "name": "Dota 2: BetBoom Team vs Team Spirit (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "26540006919120334808974189348780198846859795062245994778270066198146600210699",
        "no_token_id": "58324858838319791363508955088406647263662898089846320574702954641482049883266",
        "yes_team": "BetBoom Team",
        "no_team": "Team Spirit",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-bb4-ts8-2026-05-26"
      },
      {
        "name": "Dota 2: PARIVISION vs OG (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "67301628084023873601702813466895743995576795653464649055477981834828517685877",
        "no_token_id": "97804566911305907300566651449777108520466994372903791124083465272525405286607",
        "yes_team": "PARIVISION",
        "no_team": "OG",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-pari-og-2026-05-26"
      },
      {
        "name": "Dota 2: PARIVISION vs Team Yandex (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "62705920460289440014712656119328357331983917170769112774889622161912219226313",
        "no_token_id": "85223060305227818521045565414194424670038042342162266070065829123358163144357",
        "yes_team": "PARIVISION",
        "no_team": "Team Yandex",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-pari-ty-2026-05-26"
      },
      {
        "name": "Dota 2: Tundra Esports vs Team Spirit (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "110194778291685752199679912248286758779098964058491033080411480338405844483112",
        "no_token_id": "37884471047796888606535906870917736216100387210731544012383806850150197008584",
        "yes_team": "Tundra Esports",
        "no_team": "Team Spirit",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-tundra-ts8-2026-05-26"
      },
      {
        "name": "Dota 2: Team Spirit vs Team Yandex (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "61898048766300539207144252096568571752873123869347775462223351981847097805718",
        "no_token_id": "31845245749285637409541614747824705305729904671688525802437829597333631327920",
        "yes_team": "Team Spirit",
        "no_team": "Team Yandex",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-ts8-ty-2026-05-26"
      },
      {
        "name": "Dota 2: Team Liquid vs Team Yandex (BO1) - BLAST Slam Group Stage",
        "yes_token_id": "83719396851279461976590683957452617066433773963810098055010329649741441258449",
        "no_token_id": "71561016792382290782863982737239952010244996413668435735269793102705273107848",
        "yes_team": "Team Liquid",
        "no_team": "Team Yandex",
        "market_type": "MATCH_WINNER",
        "source_url": "https://polymarket.com/esports/dota-2/blast-slam/dota2-liquid-ty-2026-05-26"
      }
    ]

    with open("markets.yaml", "r") as f:
        data = yaml.safe_load(f) or {"markets": []}
    
    existing_ids = {str(m.get("yes_token_id")) for m in data["markets"]}
    
    new_added = 0
    for res in discovery_results:
        if str(res["yes_token_id"]) in existing_ids:
            continue
            
        entry = {
            "name": res["name"],
            "yes_token_id": res["yes_token_id"],
            "no_token_id": res["no_token_id"],
            "market_type": res["market_type"],
            "yes_team": res["yes_team"],
            "no_team": res["no_team"],
            "outcome_order_verified": True,
            "dota_match_id": "STEAM_MATCH_OR_LOBBY_ID_HERE",
            "confidence": 0.0,
            "source_url": res["source_url"]
        }
        data["markets"].append(entry)
        new_added += 1
        
    with open("markets.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        
    print(f"Added {new_added} new BLAST markets to markets.yaml")

if __name__ == "__main__":
    main()
