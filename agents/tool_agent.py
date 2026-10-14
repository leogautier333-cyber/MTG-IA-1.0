import json
import os
import re

import requests

from agents.tools import TOOL_SCHEMAS, execute_tool, serialize_tool_result


SYSTEM_PROMPT = (
    "Tu es le copilote Magic Trading. Utilise toujours un outil avant de répondre sur les cartes, "
    "prix, portefeuille ou risques. N'invente aucun chiffre : restitue uniquement les données "
    "des outils, signale explicitement les données manquantes et distingue estimation et fait observé."
)


class ToolCallingAgent:
    def __init__(self, api_key=None, model=None, base_url=None, session=requests):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.session = session

    def answer(self, user_query, history=None):
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY n'est pas configurée.")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend((history or [])[-8:])
        messages.append({"role": "user", "content": user_query})
        evidence = []

        for attempt in range(5):
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "tools": TOOL_SCHEMAS,
                    "tool_choice": "required" if attempt == 0 else "auto",
                },
                timeout=45,
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                answer = message.get("content") or "Je n'ai pas reçu de réponse exploitable."
                if self._contains_unverified_number(answer, evidence):
                    return "Données des outils : " + " ; ".join(evidence)
                return answer

            messages.append(message)
            for call in calls:
                function_call = call.get("function", {})
                name = function_call.get("name", "")
                try:
                    arguments = json.loads(function_call.get("arguments") or "{}")
                    result = execute_tool(name, arguments)
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    result = {"error": str(error)}
                serialized = serialize_tool_result(result)
                evidence.append(serialized)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": serialized,
                })
        return "Données des outils : " + " ; ".join(evidence)

    @staticmethod
    def _contains_unverified_number(answer, evidence):
        numbers = re.findall(r"(?<![\w])\d+(?:[.,]\d+)?", answer)
        evidence_numbers = {
            float(number.replace(",", "."))
            for source in evidence
            for number in re.findall(r"(?<![\w])\d+(?:[.,]\d+)?", source)
        }
        return any(float(number.replace(",", ".")) not in evidence_numbers for number in numbers)