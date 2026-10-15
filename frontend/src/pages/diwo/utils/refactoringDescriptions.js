/**
 * refactoringDescriptions.js
 * ==========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * One plain-language sentence per refactoring method, shown under the method
 * heading in the plan step list. These are textbook definitions of the
 * technique (Fowler's catalogue), not facts about the plan — a method not
 * listed here simply shows no description.
 */

const DESCRIPTIONS = {
  "introduce constant": "Replaces magic numbers and string literals with clearly named constants.",
  "replace magic number with symbolic constant": "Replaces magic numbers and string literals with clearly named constants.",
  "encapsulate variable": "Limits where a variable can be read or changed by putting access behind a smaller scope.",
  "extract method": "Moves a block of code into its own well-named method to shorten and clarify the original.",
  "extract function": "Moves a block of code into its own well-named function to shorten and clarify the original.",
  "inline method": "Replaces a call with the method's body when the method adds no clarity of its own.",
  "extract class": "Splits a class that does too much by moving related fields and methods into a new class.",
  "inline class": "Folds a class that does too little back into the class that uses it.",
  "move method": "Moves a method to the class whose data it uses most.",
  "move field": "Moves a field to the class that uses it most.",
  "rename variable": "Gives a variable a name that says what it holds.",
  "rename method": "Gives a method a name that says what it does.",
  "introduce parameter object": "Groups parameters that travel together into a single object.",
  "replace conditional with polymorphism": "Replaces a type-based conditional with subclasses that each handle one case.",
  "decompose conditional": "Moves the parts of a complex condition into well-named methods.",
  "hide delegate": "Adds a method on the client-facing class so callers no longer reach through a chain of objects.",
  "remove middle man": "Lets callers use the delegate directly when a class only forwards calls.",
  "collapse hierarchy": "Merges a subclass into its parent when they are no longer meaningfully different.",
  "extract superclass": "Moves features shared by two classes into a common parent class.",
  "extract interface": "Declares the shared subset of a class's methods as an interface.",
  "pull up method": "Moves identical methods from subclasses into the parent class.",
  "push down method": "Moves a method from the parent into the only subclasses that use it.",
  "remove dead code": "Deletes code that is never executed or never used.",
  "replace temp with query": "Replaces a temporary variable with a method that computes its value.",
  "split variable": "Gives each separate use of a reused variable its own variable.",
};

/** The description for a refactoring method, or null when none is known. */
export const describeRefactoring = (method) =>
  DESCRIPTIONS[String(method || "").trim().toLowerCase()] || null;
