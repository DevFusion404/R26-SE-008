public class MagicNumber {

    public double calculateCircleArea(double radius) {
        // 3.14 is a magic number (not in safe set)
        return 3.14 * radius * radius;
    }

    public int getTimeout() {
        // 30 is a magic number
        return 30;
    }

    public int safeZero() {
        return 0; // NOT magic — in safe set
    }

    public int safeOne() {
        return 1; // NOT magic — in safe set
    }
}
