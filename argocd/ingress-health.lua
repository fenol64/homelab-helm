-- Traefik neste homelab publica na porta 80 do nó e não preenche status.loadBalancer.
-- Sem isto o Argo CD deixa a Application em Progressing para sempre.
-- Esta função está aplicada em argocd-cm, chave
-- resource.customizations.health.networking.k8s.io_Ingress.
hs = {}
if obj.spec ~= nil and (obj.spec.ingressClassName == "traefik" or (obj.spec.ingressClassName == "tailscale" and obj.status ~= nil and obj.status.loadBalancer ~= nil and obj.status.loadBalancer.ingress ~= nil)) then
  hs.status = "Healthy"
  return hs
end
if obj.status ~= nil and obj.status.loadBalancer ~= nil and obj.status.loadBalancer.ingress ~= nil then
  hs.status = "Healthy"
  return hs
end
hs.status = "Progressing"
hs.message = "Waiting for load balancer"
return hs
