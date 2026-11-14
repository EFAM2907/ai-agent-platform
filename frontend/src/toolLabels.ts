// Nombre legible de cada herramienta del agente, para mostrar qué está
// haciendo mientras responde ("Consultando envíos…") y en el detalle de
// una respuesta. Una herramienta nueva sin entrada aquí se muestra con su
// nombre técnico: nunca se rompe, solo se ve menos amable.
const TOOL_LABELS: Record<string, string> = {
  whoami: "Verificando tu perfil",
  create_user: "Creando un usuario",
  list_users: "Consultando usuarios",
  get_user: "Consultando un usuario",
  update_user: "Actualizando un usuario",
  delete_user: "Eliminando un usuario",
  change_user_role: "Cambiando un rol",
  list_shipments: "Consultando envíos",
  get_shipment_status: "Consultando el estado de un envío",
  get_shipment_history: "Revisando el historial del envío",
  shipment_stats: "Calculando estadísticas de envíos",
  list_delayed_shipments: "Buscando envíos demorados",
  list_customers: "Consultando clientes",
  get_customer: "Consultando un cliente",
  create_customer: "Registrando un cliente",
  update_customer: "Actualizando un cliente",
  create_shipment: "Creando un envío",
  update_shipment_status: "Actualizando el estado del envío",
  notify_shipment_customer: "Enviando un aviso al cliente",
  list_claims: "Consultando reclamos",
  get_claim: "Consultando un reclamo",
  open_claim: "Abriendo un reclamo",
  update_claim_status: "Actualizando un reclamo",
  search_knowledge_base: "Buscando en la base de conocimiento",
  list_kb_documents: "Revisando los documentos",
  gmail_status: "Revisando Gmail",
  list_unresolved_emails: "Revisando correos sin resolver",
  poll_gmail_now: "Revisando el buzón de Gmail",
  drive_status: "Revisando Google Drive",
  sync_drive: "Sincronizando Google Drive",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name;
}
