/** Ответ POST /api/v1/auth/link/request — заявка ссылки-подписки на почту. */
export interface LinkRequestResponse {
  ok: boolean;
  sent: boolean;
}
