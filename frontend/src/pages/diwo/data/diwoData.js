export const CUQA_DATA = {
  summary: { files_analyzed: 50, total_lines_of_code: 3015, total_code_smells: 47, smell_severity: { high: 7, medium: 0, low: 40 }, average_quality_score: 98.1 },
  files: [
    { file: "__init__.py", language: "python", metrics: { lines_of_code: 24, functions: 1, classes: 0 }, code_smells: [{ type: "MagicNumber", message: "Magic number 6", line: 3, severity: "low" }], quality_score: 99, relative_path: "django/__init__.py" },
    { file: "config.py", language: "python", metrics: { lines_of_code: 274, functions: 10, classes: 1 }, code_smells: [{ type: "LongMethod", message: "Function '__init__' has 40 lines (>30)", line: 16, severity: "high" }, { type: "LongMethod", message: "Function 'create' has 122 lines (>30)", line: 100, severity: "high" }], quality_score: 94, relative_path: "django/apps/config.py" },
    { file: "registry.py", language: "python", metrics: { lines_of_code: 438, functions: 21, classes: 1 }, code_smells: [{ type: "LargeClass", message: "Class 'Apps' has 21 methods (>15)", line: 13, severity: "high" }, { type: "LongMethod", message: "Function '__init__' has 39 lines (>30)", line: 20, severity: "high" }], quality_score: 92, relative_path: "django/apps/registry.py" },
    { file: "base.py", language: "python", metrics: { lines_of_code: 189, functions: 8, classes: 2 }, code_smells: [{ type: "MagicNumber", message: "Magic number 100", line: 45, severity: "low" }, { type: "MagicNumber", message: "Magic number 255", line: 78, severity: "low" }], quality_score: 97, relative_path: "django/conf/base.py" },
    { file: "checks.py", language: "python", metrics: { lines_of_code: 312, functions: 15, classes: 3 }, code_smells: [{ type: "LongMethod", message: "Function 'check_settings' has 65 lines (>30)", line: 22, severity: "high" }, { type: "DuplicateCode", message: "Duplicated block detected", line: 88, severity: "low" }], quality_score: 93, relative_path: "django/core/checks.py" },
    { file: "handlers.py", language: "python", metrics: { lines_of_code: 201, functions: 12, classes: 2 }, code_smells: [{ type: "MagicNumber", message: "Magic number 404", line: 33, severity: "low" }, { type: "MagicNumber", message: "Magic number 500", line: 55, severity: "low" }], quality_score: 97, relative_path: "django/core/handlers.py" },
    { file: "utils.py", language: "python", metrics: { lines_of_code: 156, functions: 9, classes: 0 }, code_smells: [{ type: "LongMethod", message: "Function 'parse_request' has 48 lines (>30)", line: 67, severity: "high" }], quality_score: 95, relative_path: "django/utils/utils.py" },
  ]
};

export const PLAN_DATA = {
  plan_id: "plan_20260505_221716",
  target: "ECommerceSystem",
  summary: "39-step plan addressing 39 of 50 detected smells in ECommerceSystem. Refactorings applied: Extract Method, Extract Class, Move Method, Replace Conditional with Polymorphism, Introduce Parameter Object, Hide Delegate, Inline Class, Collapse Hierarchy.",
  steps: [
    { step_id: 1, refactoring: "Extract Method", smell_id: "smell_108", explanation: "Extract Method on UserService.processImport to address Long Method smell. Expected high impact with low risk and low complexity. Metrics: 220 lines, cyclomatic complexity 35, lines 300-520.", parameters: { new_method_name: "extracted_processImport", source_lines: [300, 520] }, target: { class: "UserService", method: "processImport" }, impact: "high", risk: "low" },
    { step_id: 2, refactoring: "Extract Class", smell_id: "smell_002", explanation: "Extract Class on OrderProcessor to address God Class smell. Expected high impact with medium risk and high complexity. Metrics: 850 lines, 25 methods, lines 1-850.", parameters: { new_class_name: "OrderProcessorHelper", source_class: "OrderProcessor" }, target: { class: "OrderProcessor", method: null }, impact: "high", risk: "medium" },
    { step_id: 3, refactoring: "Move Method", smell_id: "smell_106", explanation: "Move Method on UserService.updateEmail to address Shotgun Surgery smell. High impact, medium risk. 40 lines, lines 250-290.", parameters: { method: "updateEmail", source_class: "UserService", destination_class: "EmailService" }, target: { class: "UserService", method: "updateEmail" }, impact: "high", risk: "medium" },
    { step_id: 4, refactoring: "Extract Class", smell_id: "smell_003", explanation: "Extract Class on PaymentGateway to address God Class smell. High impact, medium risk. 1100 lines, 32 methods.", parameters: { new_class_name: "PaymentGatewayHelper", source_class: "PaymentGateway" }, target: { class: "PaymentGateway", method: null }, impact: "high", risk: "medium" },
    { step_id: 5, refactoring: "Introduce Parameter Object", smell_id: "smell_045", explanation: "Introduce Parameter Object on OrderProcessor.calculateTotal to address Long Parameter List smell. Low risk, medium complexity.", parameters: { new_class_name: "OrderParams", source_method: "calculateTotal" }, target: { class: "OrderProcessor", method: "calculateTotal" }, impact: "medium", risk: "low" },
    { step_id: 6, refactoring: "Replace Conditional with Polymorphism", smell_id: "smell_077", explanation: "Replace Conditional with Polymorphism on ShippingService.calculateShipping. Medium impact, medium risk.", parameters: { target_method: "calculateShipping" }, target: { class: "ShippingService", method: "calculateShipping" }, impact: "medium", risk: "medium" },
    { step_id: 7, refactoring: "Hide Delegate", smell_id: "smell_031", explanation: "Hide Delegate on ReportGenerator to reduce Message Chain smell. Low risk, low complexity.", parameters: { target_class: "ReportGenerator" }, target: { class: "ReportGenerator", method: null }, impact: "low", risk: "low" },
    { step_id: 8, refactoring: "Inline Class", smell_id: "smell_019", explanation: "Inline Class on TaxCalculatorWrapper (Lazy Class). Low risk.", parameters: { source_class: "TaxCalculatorWrapper" }, target: { class: "TaxCalculatorWrapper", method: null }, impact: "low", risk: "low" },
  ]
};

export const SCTVA_DATA = {
  success: true, confidence_score: 1.0, language: "java", rollback_occurred: false,
  confidence_components: { syntax_component: 1, structural_component: 0.9999, behavioral_component: 1, invariant_component: 1, invariant_weight: 0.15, weights: { behavioral: 0.3, structural: 0.35, syntax: 0.35 } },
  safety_report: {
    summary: "Transformation accepted after all safety checks.",
    risk_flags: [],
    rollback_reason: "",
    human_messages: ["Invariant mining: Java invariants preserved.", "Preserved invariants: execution_success_consistency, return_type_consistency, non_null_return_consistency, exception_pattern_consistency"]
  },
  validation: {
    syntax: { passed: true, details: "Compilation successful. No syntax errors." },
    structural: { passed: true, details: "AST structural integrity maintained." },
    behavioral: { passed: true, details: "10 Java behavioral runtime probe(s) executed. All passed.", fingerprint_status: "passed" },
    invariant: { passed: true, details: "All program invariants preserved post-transformation." }
  }
};

export const REFACTORED_CODE_SNIPPET = `import java.time.LocalDate;
import java.util.*;

/**
 * Refactored ECommerceSystem — DIWO Agent Output
 * Applied: Extract Method, Extract Class, Move Method,
 * Replace Conditional with Polymorphism, Introduce Parameter Object
 */
public class ECommerceSystem {
    public static void main(String[] args) {
        Customer customer = new Customer(1, "Pasan", "pasan@example.com", "premium", "Colombo");
        Order order = new Order(1001, customer);
        order.items.add(new OrderItem("Laptop", 2, 1200.00));
        order.items.add(new OrderItem("Mouse", 1, 30.00));

        OrderProcessorHelper processor = new OrderProcessorHelper();
        OrderParams params = new OrderParams("CARD", true, "PROMO10", "EXPRESS");
        double total = processor.extracted_calculateTotal(order, params);
        System.out.println("Order Total: " + total);

        UserService userService = new UserService();
        RegisterUserParams registerParams = new RegisterUserParams(
            "pasan", "pasan@example.com", "P@ssword123",
            "Pasan", "Amarasinghe", "0771234567"
        );
        userService.registerUser(registerParams);
    }
}

class OrderProcessorHelper {
    public double extracted_calculateTotal(Order order, OrderParams params) {
        double subtotal = calculateSubtotal(order.items);
        double discount = applyPromoCode(subtotal, params.promoCode);
        double shipping = ShippingStrategy.forType(params.shippingType).calculate(order);
        double tax = TaxCalculator.compute(subtotal - discount);
        return subtotal - discount + shipping + tax;
    }

    private double calculateSubtotal(List<OrderItem> items) {
        return items.stream().mapToDouble(i -> i.price * i.quantity).sum();
    }

    private double applyPromoCode(double amount, String code) {
        if ("PROMO10".equals(code)) return amount * 0.10;
        return 0;
    }
}`;
