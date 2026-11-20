# Homelab ARR Stack (Talos + Helm)

Chart Helm enxuto para streaming no homelab: **Jellyfin** + **Jellyseerr** (busca e pedidos) + **Radarr** + **Sonarr** + **Prowlarr** + **qBittorrent**.

Pensado para Talos OS com ~12 GiB de RAM. A GeForce 520 **não** é usada para transcode (sem NVENC útil) — priorize direct play.

## O que faz

1. Você abre o **Jellyseerr**, pesquisa um filme/série e pede
2. **Radarr/Sonarr** criam o item
3. **Prowlarr** encontra releases nos indexers
4. **qBittorrent** baixa para `/downloads`
5. O arquivo vai para `/media/...` e aparece no **Jellyfin**

## Pré-requisitos

- Cluster Kubernetes (Talos)
- [Helm 3](https://helm.sh/docs/intro/install/)
- `kubectl` apontando para o cluster
- Um **StorageClass** (ex.: `local-path`, Longhorn, Mayastor)

### Instalar o Helm (se ainda não tiver)

```bash
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
```

## Instalar a stack

Ajuste o StorageClass em `homelab-arr/values-homelab.yaml` e rode:

```bash
helm upgrade --install media ./homelab-arr \
  -n media --create-namespace \
  -f ./homelab-arr/values-homelab.yaml
```

Verificar:

```bash
kubectl -n media get pods,svc,pvc
helm -n media status media
```

## Acessar (sem Ingress)

```bash
kubectl -n media port-forward svc/media-homelab-arr-jellyfin 8096:8096
kubectl -n media port-forward svc/media-homelab-arr-jellyseerr 5055:5055
kubectl -n media port-forward svc/media-homelab-arr-radarr 7878:7878
kubectl -n media port-forward svc/media-homelab-arr-sonarr 8989:8989
kubectl -n media port-forward svc/media-homelab-arr-prowlarr 9696:9696
kubectl -n media port-forward svc/media-homelab-arr-qbittorrent 8080:8080
```

(Os nomes dos Services seguem `{release}-homelab-arr-{app}`; confira com `kubectl -n media get svc`.)

## Setup pós-install (uma vez)

Ordem recomendada:

1. **qBittorrent** — senha da WebUI; categoria/pasta em `/downloads`
2. **Prowlarr** — indexers → Apps → adicionar Radarr e Sonarr (sync)
3. **Radarr** — Root Folder `/media/movies` + Download Client apontando para o Service do qBittorrent
4. **Sonarr** — Root Folder `/media/tv` + mesmo download client
5. **Jellyfin** — bibliotecas Movies/TV em `/media/movies` e `/media/tv`
6. **Jellyseerr** — conectar Jellyfin + Radarr + Sonarr

Depois disso, a busca de filmes fica no Jellyseerr — sem cadastro manual no Radarr.

### Hostnames internos (quando conectar os apps)

Dentro do cluster:

| App | Host DNS do Service |
|-----|---------------------|
| qBittorrent | `media-homelab-arr-qbittorrent.media.svc.cluster.local:8080` |
| Radarr | `media-homelab-arr-radarr.media.svc.cluster.local:7878` |
| Sonarr | `media-homelab-arr-sonarr.media.svc.cluster.local:8989` |
| Prowlarr | `media-homelab-arr-prowlarr.media.svc.cluster.local:9696` |
| Jellyfin | `media-homelab-arr-jellyfin.media.svc.cluster.local:8096` |

## Recursos (defaults ~12 GiB)

| App | Request | Limit |
|-----|---------|-------|
| Jellyfin | 512Mi | 1.5Gi |
| Jellyseerr | 128Mi | 384Mi |
| Radarr | 256Mi | 512Mi |
| Sonarr | 256Mi | 512Mi |
| Prowlarr | 128Mi | 384Mi |
| qBittorrent | 256Mi | 768Mi |

Idle típico ~2–3 GiB; sobra RAM para outras cargas no nó.

## Ingress (opcional)

Em `values.yaml` / override:

```yaml
ingress:
  enabled: true
  className: traefik   # ou nginx
jellyfin:
  ingress:
    enabled: true
    host: jellyfin.home.arpa
jellyseerr:
  ingress:
    enabled: true
    host: seerr.home.arpa
```

## Desinstalar

```bash
helm -n media uninstall media
# PVCs podem ficar — apague manualmente se quiser limpar dados:
# kubectl -n media delete pvc --all
```

## Estrutura

```
homelab-arr/
  Chart.yaml
  values.yaml
  values-homelab.yaml
  templates/
```

## Limitações

- Sem VPN sidecar no qBittorrent (pode ser follow-up)
- Sem Bazarr / Lidarr no v1 (`enabled` fácil de estender depois)
- Sem GPU / HW accel na GeForce 520
