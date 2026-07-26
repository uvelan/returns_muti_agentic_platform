import { type ScenarioPort } from "../ports/scenariosPort";
import { HttpScenarioAdapter } from "./httpScenariosAdapter";

export function createScenarioAdapters(): ScenarioPort {
  return new HttpScenarioAdapter();
}
