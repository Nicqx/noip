# No-IP – egyetlen aktív DDNS-frissítő

A DDNS a publikus internetcímet követi. A Pi5 `192.168.1.6` → NUC `192.168.1.10` váltás miatt a No-IP hostname-et nem kell átírni; a router továbbítási célja változik.

A NUC-on meglévő `noip-ddns-updater`, `noip-ddns-config`, `noip-ddns-secret` neveket és az aktív/álló állapotot megőrizzük. Új telepítés – így a jelenlegi Pi5 – **0 replikával indul**, amíg külön nem engedélyezed. A megadott leltár szerint a NUC frissítője már aktív; mellé a Pi-n ne indíts másodikat.

A shellből betöltött credential-fájl helyett Python standard library alapú HTTPS-kliens dolgozik. A régi `credentials.conf` is olvasható, de soha nem fut le shellkódként. Az image digesttel rögzített, ARM64/AMD64-képes; a folyamat nem root, fájlrendszere írásvédett, a Kubernetes API-token nincs bemountolva.

## Közös használat

A parancsokat a repo könyvtárából futtasd. Előfeltétel: Bash, Git, Python 3.9+ és a helyi clusterhez hozzáférő `kubectl` (az ingresshez OpenSSL is kell). Python-csomag telepítése nem szükséges.

```bash
git pull --ff-only
./update.sh --target pi5 --dry-run
./update.sh --target pi5
```

A NUC-on `--target nuc` kell. A parancs ellenőrzi a node nevét és architektúráját; többnode-os clusterhez szándékosan nincs automatikus telepítés. Más kube-context: `--context NEV`. Ha a kubeconfig csak sudo-val olvasható, a teljes script helyett csak a kubectl kapjon jogosultságot:

```bash
KUBECTL='sudo k3s kubectl' ./update.sh --target nuc
```

Az update tiszta munkakönyvtárban `git pull --ff-only` után dolgozik. A `--dry-run` a jelenlegi helyi kódot ellenőrzi a Kubernetes API-val, nem pullol és nem ír a clusterbe. A tudatosan helyi kódhoz `--no-pull` használható. Nincs `reset --hard`, force push, prune vagy tömeges erőforrás-törlés.

Diagnosztika és kapcsolat nélküli manifest-előnézet:

```bash
./scripts/diagnose.sh --target pi5
python3 scripts/manage.py render --target nuc
```

A módosítás előtt a korábbi manifestek `0600` jogosultságú mentése készül a `~/.local/state/nicqx-infra/<target>/<repo>/` könyvtárba. A parancs kiírja a pontos fájlnevet. Más mentési gyökér: `NICQX_STATE_DIR`. A négy repo ugyanazon felhasználó/gép/cél műveleteit helyi zárral sorosítja; több operátor között külön egyeztetés szükséges.

Manifest-visszaállítás a **kiírt mentési fájl** teljes elérési útjával:

```bash
python3 scripts/manage.py rollback --target pi5 --file /teljes/ut/manifest-mentes.json --dry-run
python3 scripts/manage.py rollback --target pi5 --file /teljes/ut/manifest-mentes.json
```

Ez csak ugyanabban a clusterben, a repo engedélyezett erőforrásaira működik. Nem állít vissza adatbázist, és nem törli az időközben létrehozott erőforrásokat. Sikertelen rolloutnál az update hibával áll le; a visszaállítás külön, látható művelet.

Az `availability-calendar`, `availability-calendar-service`, `connectivity-check`, `munkaido`, `munkaido-nyilvantarto`, `rsvp1984`, `rsvp1985` neveket a közös eszköz védi. Más, ismeretlen erőforrásokat sem alkalmaz a repo saját engedélylistáján kívül. Dockerhez, K3s szolgáltatáshoz vagy routerhez egyik update sem nyúl.

## Beállítás és titkok

A meglévő ConfigMap hostname-jét és ellenőrzési időközét átvesszük. Felülbírálás: másold a `config.example.json` fájlt a gitből kizárt `config.local.json` névre. Új alapérték: `pmqxyz.hopto.org`, 300 másodperc. Egyedi szolgáltatói végpontnál az update megáll; a kliens kizárólag a No-IP HTTPS végpontjának küld hitelesítést.

A már meglévő Secret frissítéskor változatlan marad. Új titokhoz hozz létre a repón kívül egy `0600` jogosultságú JSON-fájlt `username` és `password` mezőkkel, a saját No-IP DDNS-adataiddal. Ne add meg a jelszót parancssori argumentumban és ne commitold.

```bash
python3 scripts/manage.py set-secret --target nuc --file "$HOME/noip-credentials.json"
./update.sh --target nuc
```

Ha az új telepítés szándékosan áll:

```bash
python3 scripts/manage.py enable --target nuc
```

Leállítás kizárólag erre a DDNS Deploymentre:

```bash
python3 scripts/manage.py disable --target pi5
```

A Secret-változást a folyamat a Kubernetes kötetfrissítése után, következő ciklusában veszi át. Meglévő Secretet nem szükséges a Pi-ről átmásolni, ha a NUC-on már a megfelelő hitelesítő adatok vannak.

## Szolgáltatói válaszok

Csak pontos `good <IPv4>` / `nochg <IPv4>` válasz számít sikernek. Változatlan IP-nél nem küld új frissítést. Hibás jogosultság/hostname, tiltott kliens vagy abuse esetén megáll a frissítéssel a beállítás javításáig. `911`, HTTP 5xx és ismeretlen frissítési hibák után legalább 30 percet vár. Átirányítást nem követ, így a Basic Auth nem jut másik végpontra. A napló nem tartalmaz jelszót vagy ellenőrizetlen szolgáltatói válaszszöveget.

Az utolsó IP és a hibaállapot `emptyDir` kötetben van: konténer-újraindításnál megmarad, podcsere esetén elvész. Emiatt sikertelen hitelesítésnél előbb az adatokat javítsd, ne a podot indítsd újra ismételten.

A korábbi három önálló YAML helyett az update generálja a konfigurációt. Ne alkalmazd a régi `secret-DELME.yaml` mintát.

Teszt: `python3 -m unittest discover -s tests -v`.

Forrás: [No-IP válaszkódok](https://www.noip.com/integrate/response).
